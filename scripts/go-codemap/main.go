// go-codemap: Goのアプリ（1つのpackage）を読み、APIごとに、handlerとmiddleware、そこから呼ぶ関数、
// 投げるSQLとtable、外へのHTTPを並べる。動かさずにsourceだけを読む（標準libraryのgo/astだけ）。
//
//	make codemap                       # webapp/*/go.modのpackageの表（Markdown）
//	make codemap CHECK=read-sql        # 読み取りのSQLを投げるAPIが残っていれば一覧にしてexit 1
//	go run ./scripts/go-codemap -dir webapp/go -json
//
// 拾うもの:
//   - route: `X.Get("/p", h)`のようなmethod名の呼び出し（chi・echo・ginの`Get`/`GET`…）、`HandleFunc`・
//     `Handle`（`"POST /p"`のようにmethodを含むpatternも）、`Method("POST", "/p", h)`。chiの`Route("/p", func)`
//     の中は前置きを付け、`With(mw)`・`Group`・`Use(mw)`のmiddlewareもそのAPIの経路に入れる。
//   - SQL: 関数の中の文字列（`+`でつないだものも）のうち、SELECT・INSERT・UPDATE・DELETE・REPLACE・WITHで
//     始まるもの。tableはFROM・JOIN・INTO・UPDATE・DELETE FROMの後の名前。
//   - 呼ぶ関数: 同じpackageで宣言した関数とmethod（名前で結ぶ。同じ名前のmethodが複数あればどれも）。
//   - 外へのHTTP: `http.NewRequest*`・`http.Get`・`http.Post`・`.Do(`。
//
// 名前でたどるので、interface越しや関数の値で呼ぶ先は拾えないことがある（表の`unresolved`に出す）。
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type function struct {
	name     string // `name`、methodなら`(Type).name`
	short    string // 呼び出しで使う名前
	position string
	body     *ast.BlockStmt
	sql      []statement
	calls    map[string]bool
	external []string
}

type statement struct {
	Text     string   `json:"sql"`
	Kind     string   `json:"kind"`
	Tables   []string `json:"tables"`
	Function string   `json:"function"`
	Position string   `json:"position"`
}

type route struct {
	Method      string      `json:"method"`
	Path        string      `json:"path"`
	Handler     string      `json:"handler"`
	Middlewares []string    `json:"middlewares,omitempty"`
	Position    string      `json:"position"`
	Functions   []string    `json:"functions"`
	SQL         []statement `json:"sql"`
	External    []string    `json:"external_http,omitempty"`
	Unresolved  []string    `json:"unresolved,omitempty"`
}

var (
	sqlStart   = regexp.MustCompile(`(?is)^\s*(SELECT|INSERT|UPDATE|DELETE|REPLACE|WITH)\b`)
	tableNames = regexp.MustCompile("(?i)\\b(?:FROM|JOIN|INTO|UPDATE)\\s+`?([A-Za-z_][A-Za-z0-9_]*)`?")
	space      = regexp.MustCompile(`\s+`)
	methods    = map[string]string{
		"Get": "GET", "Post": "POST", "Put": "PUT", "Patch": "PATCH", "Delete": "DELETE", "Head": "HEAD", "Options": "OPTIONS",
		"GET": "GET", "POST": "POST", "PUT": "PUT", "PATCH": "PATCH", "DELETE": "DELETE", "HEAD": "HEAD", "OPTIONS": "OPTIONS",
	}
	notSQLWords = map[string]bool{"select": true, "where": true, "set": true, "values": true, "dual": true}
)

type program struct {
	fset      *token.FileSet
	dir       string
	functions map[string]*function   // name → function
	byShort   map[string][]*function // 呼び出しの名前 → 候補
	routes    []*route
}

func main() {
	dir := flag.String("dir", "", "the Go package (default: the directory of webapp/*/go.mod)")
	asJSON := flag.Bool("json", false, "print JSON instead of Markdown")
	check := flag.String("check", "", "read-sql: list the APIs whose code still runs reading SQL, and exit 1 if any")
	allow := flag.String("allow", "/api/initialize", "comma-separated paths the check leaves out")
	flag.Parse()
	if *dir == "" {
		found, _ := filepath.Glob("webapp/*/go.mod")
		if len(found) == 0 {
			fail("no webapp/*/go.mod; pass -dir")
		}
		*dir = filepath.Dir(found[0])
	}
	prog, err := load(*dir)
	if err != nil {
		fail(err.Error())
	}
	prog.collectRoutes()
	prog.resolve()
	switch {
	case *check == "read-sql":
		os.Exit(prog.checkReadSQL(os.Stdout, strings.Split(*allow, ",")))
	case *check != "":
		fail("unknown check " + *check + " (read-sql)")
	case *asJSON:
		encoder := json.NewEncoder(os.Stdout)
		encoder.SetIndent("", "  ")
		_ = encoder.Encode(map[string]any{"dir": *dir, "routes": prog.routes, "tables": prog.tables()})
	default:
		prog.markdown(os.Stdout)
	}
}

func fail(message string) {
	fmt.Fprintln(os.Stderr, "go-codemap: "+message)
	os.Exit(2)
}

func load(dir string) (*program, error) {
	prog := &program{fset: token.NewFileSet(), dir: dir, functions: map[string]*function{}, byShort: map[string][]*function{}}
	packages, err := parser.ParseDir(prog.fset, dir, func(info os.FileInfo) bool {
		return !strings.HasSuffix(info.Name(), "_test.go")
	}, parser.SkipObjectResolution)
	if err != nil {
		return nil, err
	}
	for _, pkg := range packages {
		for _, file := range pkg.Files {
			for _, declaration := range file.Decls {
				decl, ok := declaration.(*ast.FuncDecl)
				if !ok || decl.Body == nil {
					continue
				}
				name := decl.Name.Name
				if decl.Recv != nil && len(decl.Recv.List) > 0 {
					name = "(" + receiver(decl.Recv.List[0].Type) + ")." + name
				}
				fn := &function{name: name, short: decl.Name.Name, position: prog.position(decl.Pos()), body: decl.Body}
				prog.functions[name] = fn
				prog.byShort[fn.short] = append(prog.byShort[fn.short], fn)
			}
		}
	}
	if len(prog.functions) == 0 {
		return nil, fmt.Errorf("no Go functions in %s", dir)
	}
	for _, fn := range prog.functions {
		fn.sql, fn.calls, fn.external = prog.scan(fn.name, fn.body)
	}
	return prog, nil
}

func receiver(expression ast.Expr) string {
	switch typed := expression.(type) {
	case *ast.StarExpr:
		return "*" + receiver(typed.X)
	case *ast.Ident:
		return typed.Name
	case *ast.IndexExpr:
		return receiver(typed.X)
	case *ast.IndexListExpr:
		return receiver(typed.X)
	}
	return "?"
}

func (p *program) position(pos token.Pos) string {
	at := p.fset.Position(pos)
	if relative, err := filepath.Rel(p.dir, at.Filename); err == nil {
		return relative + ":" + strconv.Itoa(at.Line)
	}
	return at.Filename + ":" + strconv.Itoa(at.Line)
}

// 関数の本体から、SQLの文字列、同じpackageの関数の呼び出し、外へのHTTPを拾う。
func (p *program) scan(owner string, body ast.Node) ([]statement, map[string]bool, []string) {
	var found []statement
	calls := map[string]bool{}
	var external []string
	seen := map[ast.Node]bool{}
	ast.Inspect(body, func(node ast.Node) bool {
		switch typed := node.(type) {
		case *ast.BinaryExpr:
			if text, ok := constant(typed); ok && !seen[typed] {
				markParts(typed, seen)
				found = appendSQL(found, text, owner, p.position(typed.Pos()))
			}
		case *ast.BasicLit:
			if seen[typed] {
				return true
			}
			if text, ok := constant(typed); ok {
				found = appendSQL(found, text, owner, p.position(typed.Pos()))
			}
		case *ast.CallExpr:
			switch callee := typed.Fun.(type) {
			case *ast.Ident:
				calls[callee.Name] = true
			case *ast.SelectorExpr:
				calls[callee.Sel.Name] = true
				if pkg, ok := callee.X.(*ast.Ident); ok && pkg.Name == "http" &&
					(strings.HasPrefix(callee.Sel.Name, "NewRequest") || callee.Sel.Name == "Get" || callee.Sel.Name == "Post") {
					external = append(external, p.position(typed.Pos()))
				} else if callee.Sel.Name == "Do" && len(typed.Args) == 1 {
					external = append(external, p.position(typed.Pos()))
				}
			}
		}
		return true
	})
	return found, calls, external
}

// `"a" + "b"`のように文字列だけでできた式の値。
func constant(expression ast.Expr) (string, bool) {
	switch typed := expression.(type) {
	case *ast.BasicLit:
		if typed.Kind != token.STRING {
			return "", false
		}
		value, err := strconv.Unquote(typed.Value)
		return value, err == nil
	case *ast.BinaryExpr:
		if typed.Op != token.ADD {
			return "", false
		}
		left, ok := constant(typed.X)
		if !ok {
			return "", false
		}
		right, ok := constant(typed.Y)
		return left + right, ok
	case *ast.ParenExpr:
		return constant(typed.X)
	}
	return "", false
}

func markParts(expression ast.Expr, seen map[ast.Node]bool) {
	ast.Inspect(expression, func(node ast.Node) bool {
		if node != nil {
			seen[node] = true
		}
		return true
	})
}

func appendSQL(found []statement, text, owner, position string) []statement {
	match := sqlStart.FindStringSubmatch(text)
	if match == nil {
		return found
	}
	kind := "write"
	switch strings.ToUpper(match[1]) {
	case "SELECT", "WITH":
		kind = "read"
	}
	if kind == "read" && strings.Contains(strings.ToUpper(text), " FOR UPDATE") {
		kind = "read-for-update"
	}
	var tables []string
	seen := map[string]bool{}
	for _, table := range tableNames.FindAllStringSubmatch(text, -1) {
		name := strings.ToLower(table[1])
		if !seen[name] && !notSQLWords[name] {
			seen[name] = true
			tables = append(tables, name)
		}
	}
	sort.Strings(tables)
	return append(found, statement{Text: strings.TrimSpace(space.ReplaceAllString(text, " ")), Kind: kind,
		Tables: tables, Function: owner, Position: position})
}

// routeを登録している呼び出しを、chiの`Route`の前置きとmiddlewareを持ち回りながら探す。
func (p *program) collectRoutes() {
	for _, fn := range p.functions {
		p.walkRoutes(fn.body, "", nil, map[string]routerState{})
	}
	sort.Slice(p.routes, func(i, j int) bool {
		if p.routes[i].Path != p.routes[j].Path {
			return p.routes[i].Path < p.routes[j].Path
		}
		return p.routes[i].Method < p.routes[j].Method
	})
}

type routerState struct {
	prefix      string
	middlewares []string
}

func (p *program) walkRoutes(body ast.Node, prefix string, middlewares []string, routers map[string]routerState) {
	state := func(expression ast.Expr) routerState {
		if ident, ok := expression.(*ast.Ident); ok {
			if known, ok := routers[ident.Name]; ok {
				return known
			}
		}
		return routerState{prefix: prefix, middlewares: middlewares}
	}
	ast.Inspect(body, func(node ast.Node) bool {
		switch typed := node.(type) {
		case *ast.AssignStmt:
			// `authed := mux.With(mw)`・`g := e.Group("/p", mw)`は、その変数へ前置きとmiddlewareを持たせる。
			if len(typed.Lhs) == 1 && len(typed.Rhs) == 1 {
				if name, ok := typed.Lhs[0].(*ast.Ident); ok {
					if call, ok := typed.Rhs[0].(*ast.CallExpr); ok {
						if selector, ok := call.Fun.(*ast.SelectorExpr); ok {
							base := state(selector.X)
							switch selector.Sel.Name {
							case "With":
								routers[name.Name] = routerState{base.prefix, append(append([]string(nil), base.middlewares...), names(call.Args)...)}
							case "Group":
								if len(call.Args) > 0 {
									if path, ok := constant(call.Args[0]); ok {
										routers[name.Name] = routerState{join(base.prefix, path), append(append([]string(nil), base.middlewares...), names(call.Args[1:])...)}
									}
								}
							}
						}
					}
				}
			}
		case *ast.CallExpr:
			selector, ok := typed.Fun.(*ast.SelectorExpr)
			if !ok {
				return true
			}
			base := state(selector.X)
			name := selector.Sel.Name
			switch {
			case name == "Use":
				if ident, ok := selector.X.(*ast.Ident); ok {
					current := routers[ident.Name]
					if _, known := routers[ident.Name]; !known {
						current = routerState{prefix: prefix, middlewares: middlewares}
					}
					current.middlewares = append(append([]string(nil), current.middlewares...), names(typed.Args)...)
					routers[ident.Name] = current
				}
			case name == "Route" && len(typed.Args) == 2:
				if path, ok := constant(typed.Args[0]); ok {
					if literal, ok := typed.Args[1].(*ast.FuncLit); ok {
						inner := map[string]routerState{}
						for key, value := range routers {
							inner[key] = value
						}
						if len(literal.Type.Params.List) > 0 && len(literal.Type.Params.List[0].Names) > 0 {
							inner[literal.Type.Params.List[0].Names[0].Name] = routerState{join(base.prefix, path), base.middlewares}
						}
						p.walkRoutes(literal.Body, join(base.prefix, path), base.middlewares, inner)
						return false
					}
				}
			case name == "Group" && len(typed.Args) == 1:
				if literal, ok := typed.Args[0].(*ast.FuncLit); ok {
					inner := map[string]routerState{}
					for key, value := range routers {
						inner[key] = value
					}
					if len(literal.Type.Params.List) > 0 && len(literal.Type.Params.List[0].Names) > 0 {
						inner[literal.Type.Params.List[0].Names[0].Name] = base
					}
					p.walkRoutes(literal.Body, base.prefix, base.middlewares, inner)
					return false
				}
			case name == "HandleFunc" || name == "Handle":
				if len(typed.Args) >= 2 {
					if pattern, ok := constant(typed.Args[0]); ok {
						method, path := "ANY", pattern
						if fields := strings.Fields(pattern); len(fields) == 2 {
							method, path = fields[0], fields[1]
						}
						p.addRoute(method, join(base.prefix, path), typed.Args[len(typed.Args)-1], base.middlewares, typed.Pos())
					}
				}
			case name == "Method" && len(typed.Args) == 3:
				method, ok1 := constant(typed.Args[0])
				path, ok2 := constant(typed.Args[1])
				if ok1 && ok2 {
					p.addRoute(strings.ToUpper(method), join(base.prefix, path), typed.Args[2], base.middlewares, typed.Pos())
				}
			default:
				if method, ok := methods[name]; ok && len(typed.Args) >= 2 {
					if path, ok := constant(typed.Args[0]); ok && strings.HasPrefix(path, "/") {
						// echo・ginは`GET(path, handler, mw...)`、chiは`Get(path, handler)`。handlerは2つ目。
						p.addRoute(method, join(base.prefix, path), typed.Args[1], append(append([]string(nil), base.middlewares...), names(typed.Args[2:])...), typed.Pos())
					}
				}
			}
		}
		return true
	})
}

func join(prefix, path string) string {
	if prefix == "" {
		return path
	}
	return strings.TrimSuffix(prefix, "/") + "/" + strings.TrimPrefix(path, "/")
}

// 引数の関数の名前（`mw`、`pkg.mw`、`mw(arg)`の`mw`）。
func names(arguments []ast.Expr) []string {
	var found []string
	for _, argument := range arguments {
		if name := handlerName(argument); name != "" {
			found = append(found, name)
		}
	}
	return found
}

func handlerName(expression ast.Expr) string {
	switch typed := expression.(type) {
	case *ast.Ident:
		return typed.Name
	case *ast.SelectorExpr:
		// `middleware.Logger`・`h.getUser`。前が名前なら残し、たどるときに外のpackageか同じpackageの
		// methodかを分ける。
		if ident, ok := typed.X.(*ast.Ident); ok {
			return ident.Name + "." + typed.Sel.Name
		}
		return typed.Sel.Name
	case *ast.IndexExpr:
		// `handlers[0]`のような値は、どの関数かsourceからは決まらない。
		if name := handlerName(typed.X); name != "" {
			return name + "[…]"
		}
		return "[…]"
	case *ast.CallExpr:
		// `http.HandlerFunc(h)`や`wrap(h)`は、中の関数をhandlerとして見る。
		for index := len(typed.Args) - 1; index >= 0; index-- {
			if name := handlerName(typed.Args[index]); name != "" {
				return name
			}
		}
		return handlerName(typed.Fun)
	}
	return ""
}

func (p *program) addRoute(method, path string, handler ast.Expr, middlewares []string, pos token.Pos) {
	entry := &route{Method: method, Path: path, Middlewares: middlewares, Position: p.position(pos)}
	if literal, ok := handler.(*ast.FuncLit); ok {
		name := "func@" + p.position(literal.Pos())
		fn := &function{name: name, short: name, position: p.position(literal.Pos()), body: literal.Body}
		fn.sql, fn.calls, fn.external = p.scan(name, literal.Body)
		p.functions[name] = fn
		entry.Handler = name
	} else {
		entry.Handler = handlerName(handler)
	}
	p.routes = append(p.routes, entry)
}

// routeごとに、handlerとmiddlewareから呼べる同じpackageの関数をたどり、SQLと外へのHTTPを集める。
func (p *program) resolve() {
	for _, entry := range p.routes {
		visited := map[string]bool{}
		unresolved := map[string]bool{}
		var queue []*function
		for _, start := range append([]string{entry.Handler}, entry.Middlewares...) {
			short := start
			if dot := strings.LastIndex(start, "."); dot >= 0 && !strings.HasPrefix(start, "func@") {
				short = start[dot+1:]
			}
			switch {
			case p.functions[start] != nil:
				queue = append(queue, p.functions[start])
			case len(p.byShort[short]) > 0:
				queue = append(queue, p.byShort[short]...)
			case short != start:
				// `middleware.Logger`のような外のpackageの関数。中はたどらない。
			default:
				unresolved[start] = true
			}
		}
		for len(queue) > 0 {
			fn := queue[0]
			queue = queue[1:]
			if visited[fn.name] {
				continue
			}
			visited[fn.name] = true
			entry.SQL = append(entry.SQL, fn.sql...)
			entry.External = append(entry.External, fn.external...)
			for call := range fn.calls {
				queue = append(queue, p.byShort[call]...)
			}
		}
		for name := range visited {
			entry.Functions = append(entry.Functions, name)
		}
		sort.Strings(entry.Functions)
		for name := range unresolved {
			entry.Unresolved = append(entry.Unresolved, name)
		}
		sort.Strings(entry.Unresolved)
		sort.SliceStable(entry.SQL, func(i, j int) bool { return entry.SQL[i].Position < entry.SQL[j].Position })
	}
}

type tableUse struct {
	Reads  []string `json:"read_by"`
	Writes []string `json:"written_by"`
}

func (p *program) tables() map[string]*tableUse {
	uses := map[string]*tableUse{}
	add := func(list []string, value string) []string {
		for _, existing := range list {
			if existing == value {
				return list
			}
		}
		return append(list, value)
	}
	for _, entry := range p.routes {
		label := entry.Method + " " + entry.Path
		for _, sql := range entry.SQL {
			for _, table := range sql.Tables {
				use := uses[table]
				if use == nil {
					use = &tableUse{}
					uses[table] = use
				}
				if sql.Kind == "write" {
					use.Writes = add(use.Writes, label)
				} else {
					use.Reads = add(use.Reads, label)
				}
			}
		}
	}
	return uses
}

func counts(statements []statement) (reads, writes int, tables []string) {
	seen := map[string]bool{}
	for _, sql := range statements {
		if sql.Kind == "write" {
			writes++
		} else {
			reads++
		}
		for _, table := range sql.Tables {
			if !seen[table] {
				seen[table] = true
				tables = append(tables, table)
			}
		}
	}
	sort.Strings(tables)
	return reads, writes, tables
}

func (p *program) markdown(out io.Writer) {
	fmt.Fprintf(out, "# code map: %s\n\n", p.dir)
	fmt.Fprintln(out, "go-codemap（`make codemap`）がsourceから作った表。名前でたどるので、interface越しの呼び出しは`unresolved`に出る。")
	fmt.Fprintln(out)
	fmt.Fprintln(out, "| method | path | handler | middleware | SQL（読み/書き） | table | 外へのHTTP | 関数の数 |")
	fmt.Fprintln(out, "| --- | --- | --- | --- | --- | --- | --- | --- |")
	for _, entry := range p.routes {
		reads, writes, tables := counts(entry.SQL)
		handler := "`" + entry.Handler + "`"
		if len(entry.Unresolved) > 0 {
			handler += "（unresolved: " + strings.Join(entry.Unresolved, ", ") + "）"
		}
		fmt.Fprintf(out, "| %s | `%s` | %s | %s | %d / %d | %s | %d | %d |\n", entry.Method, entry.Path, handler,
			strings.Join(entry.Middlewares, ", "), reads, writes, strings.Join(tables, ", "), len(entry.External), len(entry.Functions))
	}
	fmt.Fprintln(out)
	fmt.Fprintln(out, "## tableごとのAPI")
	fmt.Fprintln(out)
	fmt.Fprintln(out, "| table | 読むAPI | 書くAPI |")
	fmt.Fprintln(out, "| --- | --- | --- |")
	uses := p.tables()
	var names []string
	for name := range uses {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		fmt.Fprintf(out, "| %s | %s | %s |\n", name, strings.Join(uses[name].Reads, "<br>"), strings.Join(uses[name].Writes, "<br>"))
	}
	fmt.Fprintln(out)
	fmt.Fprintln(out, "## APIごとのSQL")
	for _, entry := range p.routes {
		if len(entry.SQL) == 0 && len(entry.External) == 0 {
			continue
		}
		fmt.Fprintf(out, "\n### %s %s\n\n", entry.Method, entry.Path)
		for _, sql := range entry.SQL {
			fmt.Fprintf(out, "- %s `%s` — %s (%s)\n", sql.Kind, truncate(sql.Text, 160), sql.Function, sql.Position)
		}
		for _, position := range entry.External {
			fmt.Fprintf(out, "- 外へのHTTP — %s\n", position)
		}
	}
}

func truncate(text string, limit int) string {
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	return string(runes[:limit]) + "…"
}

// 全メモリ化の後に、読み取りのSQL（SELECT。FOR UPDATEも）を投げるAPIが残っていないかを見る。
func (p *program) checkReadSQL(out io.Writer, allowed []string) int {
	allow := map[string]bool{}
	for _, path := range allowed {
		if path = strings.TrimSpace(path); path != "" {
			allow[path] = true
		}
	}
	found := 0
	for _, entry := range p.routes {
		if allow[entry.Path] {
			continue
		}
		for _, sql := range entry.SQL {
			if sql.Kind == "write" {
				continue
			}
			if found == 0 {
				fmt.Fprintln(out, "APIs that still run reading SQL:")
			}
			found++
			fmt.Fprintf(out, "%s %s: %s `%s` (%s)\n", entry.Method, entry.Path, sql.Function, truncate(sql.Text, 120), sql.Position)
		}
	}
	if found == 0 {
		fmt.Fprintln(out, "no API runs reading SQL")
		return 0
	}
	return 1
}
