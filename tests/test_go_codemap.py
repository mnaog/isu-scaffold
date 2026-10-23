"""scripts/go-codemap（make codemap）が、Goのsourceを読んでAPIごとのhandler・middleware・SQL・table・外への
HTTPを並べ、`-check read-sql`が読み取りのSQLの残りを見つけることを確かめる。goが無い端末では飛ばす。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent

# buildはしない（go/astで読むだけ）ので、外のpackageはimportしなくてよい。
APP = r'''
package main

func setup() {
	mux := chi.NewRouter()
	mux.Use(middleware.Logger)
	mux.HandleFunc("POST /api/initialize", postInitialize)
	authed := mux.With(authMiddleware)
	authed.HandleFunc("GET /api/items", getItems)
	mux.Route("/api/admin", func(r chi.Router) {
		r.Use(adminOnly)
		r.Post("/items", postItem)
	})
	e := echo.New()
	g := e.Group("/v2", authMiddleware)
	g.GET("/users/:id", getUser)
	mux.Get("/inline", func(w http.ResponseWriter, r *http.Request) {
		db.Exec("UPDATE " + "counters SET n = n + 1")
	})
	mux.Get("/dynamic", handlers[0])
}

func authMiddleware(next http.Handler) http.Handler {
	db.Get(&user, "SELECT * FROM users WHERE token = ?", token)
	return next
}

func adminOnly(next http.Handler) http.Handler { return next }

func getItems(w http.ResponseWriter, r *http.Request) { loadItems() }

func loadItems() {
	db.Select(&items, `SELECT i.* FROM items i
		JOIN shops s ON s.id = i.shop_id`)
}

func postItem(w http.ResponseWriter, r *http.Request) {
	tx.Exec("INSERT INTO items (name) VALUES (?)", name)
	notify()
}

func notify() {
	request, _ := http.NewRequest("POST", url, nil)
	client.Do(request)
}

func getUser(c echo.Context) error { return store.find() }

func (s *Store) find() error {
	return s.db.Get(&user, "SELECT * FROM users WHERE id = ? FOR UPDATE", id)
}

func postInitialize(w http.ResponseWriter, r *http.Request) {
	db.Exec("UPDATE settings SET value = ?", value)
	db.Get(&setting, "SELECT value FROM settings")
	fmt.Println("selected nothing")
}
'''


@unittest.skipUnless(shutil.which('go'), 'go is not installed')
class GoCodemapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Path(self.tmp.name) / 'go'
        self.app.mkdir()
        (self.app / 'main.go').write_text(APP)
        (self.app / 'main_test.go').write_text('package main\n\nfunc testOnly() { db.Get(&x, "SELECT 1 FROM ignored") }\n')

    def codemap(self, *args):
        env = {**os.environ, 'GOTOOLCHAIN': 'local', 'GOFLAGS': ''}
        return subprocess.run(['go', '-C', str(ROOT / 'scripts/go-codemap'), 'run', '.', '-dir', str(self.app), *args],
                              env=env, capture_output=True, text=True, timeout=300)

    def routes(self):
        result = self.codemap('-json')
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        return {f"{route['method']} {route['path']}": route for route in document['routes']}, document['tables']

    def test_routes_carry_prefixes_middlewares_sql_tables_and_outbound_calls(self):
        routes, tables = self.routes()
        self.assertEqual(sorted(routes), ['GET /api/items', 'GET /dynamic', 'GET /inline', 'GET /v2/users/:id',
                                          'POST /api/admin/items', 'POST /api/initialize'])
        items = routes['GET /api/items']
        self.assertEqual(items['handler'], 'getItems')
        self.assertEqual(items['middlewares'], ['middleware.Logger', 'authMiddleware'])
        self.assertEqual(sorted(sql['function'] for sql in items['sql']), ['authMiddleware', 'loadItems'])
        joined = next(sql for sql in items['sql'] if sql['function'] == 'loadItems')
        self.assertEqual(joined['tables'], ['items', 'shops'])
        self.assertEqual(joined['kind'], 'read')
        self.assertNotIn('\n', joined['sql'])
        admin = routes['POST /api/admin/items']
        self.assertEqual(admin['middlewares'], ['middleware.Logger', 'adminOnly'])
        self.assertEqual([sql['kind'] for sql in admin['sql']], ['write'])
        self.assertEqual(len(admin['external_http']), 2, 'NewRequest and Do')
        user = routes['GET /v2/users/:id']
        self.assertIn('(*Store).find', user['functions'])
        self.assertIn('read-for-update', [sql['kind'] for sql in user['sql']])
        inline = routes['GET /inline']
        self.assertTrue(inline['handler'].startswith('func@main.go:'))
        self.assertEqual(inline['sql'][0]['sql'], 'UPDATE counters SET n = n + 1')
        self.assertEqual(routes['GET /dynamic']['unresolved'], ['handlers[…]'])
        self.assertNotIn('ignored', tables, 'test files are left out')
        self.assertIn('POST /api/admin/items', tables['items']['written_by'])
        self.assertIn('GET /api/items', tables['items']['read_by'])

    def test_the_read_sql_check_lists_reads_outside_initialize(self):
        result = self.codemap('-check', 'read-sql')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('GET /api/items: loadItems', result.stdout)
        self.assertIn('GET /v2/users/:id: (*Store).find', result.stdout)
        self.assertNotIn('/api/initialize', result.stdout)
        (self.app / 'main.go').write_text(APP.replace('SELECT', 'UPDATE'))
        result = self.codemap('-check', 'read-sql')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('no API runs reading SQL', result.stdout)

    def test_markdown_has_the_route_and_table_tables(self):
        result = self.codemap()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('| GET | `/api/items` | `getItems` |', result.stdout)
        self.assertIn('## tableごとのAPI', result.stdout)


if __name__ == '__main__':
    unittest.main()
