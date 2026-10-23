.PHONY: help session discover kickoff kickoff-apply build deploy rollback status bench-check first-bench tare durability pgo codemap lanes proposals builder-images isuscope-node final-check final-reboot final-db-late

help:
	@printf '%s\n' \
		'make session ROLE=<main|improve|portal|human>  役割の名前を付けたClaude Codeを、その作業場所で起動する（mainはボードも）' \
		'make discover                         providerからnodeと全設定を再生成する' \
		'make kickoff                          コード先行回収・kickoff buildからdraft生成と検査まで進める' \
		'make lanes                            claude -pのAI lane（indexes、local-env、scenarioなど）の状態を見る' \
		'make worktree BRANCH=<name> PURPOSE="..."  目的つきで並行laneのworktreeを作る' \
		'make proposals                        ベンチ待ちのキュー（worktreeからの投稿）と、その状態を見る' \
		'CONFIRM_DRAFT=true make kickoff-apply  検査済みdraftを反映して完全importする' \
		'make local-up / local-check / local-down  worktree専用のローカル実行環境を操作する' \
		'make local-flow FLOW=<json> [FROM=<前のflow>] [PARALLEL=<N>]  決まった操作の列をローカルのアプリへ流す（N本並行も）' \
		'LOCAL_RACE=1 make local-up → make local-race  race detector付きで動かし、data raceの報告を読む' \
		'make local-state-log NAME=<名前>       ローカルのアプリから状態ログを取り出す' \
		'make build                            ローカルでLinux向けGoバイナリを準備する' \
		'make builder-images                   準備: Go toolchainと両targetの標準libraryを事前にbuildする' \
		'make isuscope-node                    準備: nodeでaccess logを集計するisuscope-nodeをbuildする' \
		'make deploy                           commit済みのlocal状態を全nodeへ反映する' \
		'make rollback RELEASE=<id>            deploy前のremote状態へ戻す' \
		'make status                           全nodeを並列に検査する（様子とstatus_commands、20秒で打ち切り）' \
		'make final-check                      終了前: 再起動せずに追試で落ちる原因を確かめる' \
		'make final-reboot                     終了前: 全台を再起動し、戻った後と数分後を確かめる' \
		'make final-db-late                    終了前: DBが後から起動してもアプリが立ち直るか確かめる' \
		'make bench-check                      ベンチ前の全node・isuscope検査を行う' \
		'make first-bench                      import commit後にbench-check・初回survey-run・deployを続けて行う' \
		'make tare                             baseline後に接続・fdの上限を上げる1 commitを作る' \
		'make durability                       DBの書き込み待ちが見えたらMySQLの耐久性を緩める1 commitを作る' \
		'make pgo RUN=<run> [NODE=<node>]      runで取ったGoのCPU profileをPGOのprofile（default.pgo）にする' \
		'make codemap [CHECK=read-sql] [JSON=1]  GoのアプリのAPI・handler・SQL・tableの対応表を出す' \
		'make board                            人間向けボードを開く (127.0.0.1:8765)' \
		'make test                             問題と実サーバーを使わないテストを実行する（isu-scaffoldだけ。大会用repoにtests/は無い）'

session:
	@./scripts/session.sh "$(ROLE)" $(if $(PROMPT),"$(PROMPT)")

discover:
	@./scripts/discover.sh

kickoff:
	@./scripts/kickoff.sh

lanes:
	@python3 scripts/lane.py status

proposals:
	@python3 scripts/proposal.py list

builder-images:
	@python3 scripts/local-build.py prepare-toolchain

isuscope-node:
	@./scripts/build-isuscope-node.sh

worktree:
	@test -n "$(BRANCH)" || { echo 'BRANCH=<name>を指定してください' >&2; exit 2; }
	@test -n "$(PURPOSE)" || { echo 'PURPOSE="何をするlaneか"を指定してください' >&2; exit 2; }
	@./scripts/worktree.sh "$(BRANCH)" "$(PURPOSE)" "$(or $(BASE),main)"

kickoff-apply:
	@./scripts/kickoff-apply.sh

build:
	@python3 scripts/local-build.py build --manifest "$(or $(SYNC_MANIFEST),config/sync.json)"

deploy:
	@./scripts/deploy.sh

rollback:
	@test -n "$(RELEASE)" || { echo 'RELEASE=<id>を指定してください' >&2; exit 2; }
	@./scripts/rollback.sh "$(RELEASE)"

status:
	@./scripts/status.sh

final-check:
	@python3 scripts/final-verification.py check

final-reboot:
	@python3 scripts/final-verification.py reboot --yes

final-db-late:
	@python3 scripts/final-verification.py db-late --yes

tare:
	@python3 scripts/tare.py

durability:
	@python3 scripts/durability.py

pgo:
	@test -n "$(RUN)" || { echo 'RUN=<run ID|short ID|latest>を指定してください' >&2; exit 2; }
	@python3 scripts/pgo.py "$(RUN)" $(if $(NODE),--node $(NODE))

codemap:
	@app=$${DIR:-$$(dirname $$(ls webapp/*/go.mod | head -n 1))}; case $$app in /*) ;; *) app="$(CURDIR)/$$app" ;; esac; \
		GOTOOLCHAIN=local GOFLAGS= go -C scripts/go-codemap run . -dir "$$app" $(if $(CHECK),-check $(CHECK)) $(if $(JSON),-json)

bench-check:
	@./scripts/bench-check.sh

first-bench:
	@./scripts/first-bench.sh

.PHONY: board test
board:
	@python3 scripts/board/main.py

test:
	@if [ -d tests ]; then python3 -m unittest discover -s tests -p 'test_*.py' -v; \
		else echo 'tests/はisu-scaffoldにだけある（大会用repoには入れない）。testはisu-scaffoldで回す'; fi

.PHONY: local-up local-check local-down local-logs local-reset local-state-log local-flow local-race
local-up:
	@python3 scripts/local-runtime.py up
local-check:
	@python3 scripts/local-runtime.py check
local-down:
	@python3 scripts/local-runtime.py down
local-logs:
	@python3 scripts/local-runtime.py logs
local-reset:
	@python3 scripts/local-runtime.py reset
local-state-log:
	@python3 scripts/local-runtime.py state-log $(NAME)
local-flow:
	@python3 scripts/local-flow.py $(FLOW) $(if $(FROM),--continue $(FROM)) $(if $(PARALLEL),--parallel $(PARALLEL))
local-race:
	@python3 scripts/local-runtime.py race

.PHONY: preparation-check
preparation-check:
	@python3 scripts/preparation-check.py
