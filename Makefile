.PHONY: help discover kickoff kickoff-apply build deploy rollback status bench-check first-bench tare lanes builder-images

help:
	@printf '%s\n' \
		'make discover                         providerからnodeと全設定を再生成する' \
		'make kickoff                          コード先行回収・初期buildからdraft生成と検査まで進める' \
		'make lanes                            claude -pのAI lane（code-map、indexes、local-envなど）の状態を見る' \
		'make worktree BRANCH=<name> PURPOSE="..."  目的つきで並行laneのworktreeを作る' \
		'CONFIRM_DRAFT=true make kickoff-apply  検査済みdraftを反映して完全importする' \
		'make local-up / local-check / local-down  worktree専用のローカル実行環境を操作する' \
		'make local-flow FLOW=<json> [FROM=<前のflow>]  決まった操作の列をローカルのアプリへ流す' \
		'make local-state-log NAME=<名前>       ローカルのアプリから状態ログを取り出す' \
		'make build                            ローカルでLinux向けRust成果物を準備する' \
		'make builder-images                   準備: 候補baseのRust builder imageを事前に作る' \
		'make deploy                           commit済みのlocal状態を全nodeへ反映する' \
		'make rollback RELEASE=<id>            deploy前のremote状態へ戻す' \
		'make status                           全application nodeを検査する' \
		'make bench-check                      ベンチ前の全node・isuscope検査を行う' \
		'make first-bench                      import commit後にbench-check・初回survey-run・deployを続けて行う' \
		'make tare                             baseline後に接続・fdの上限を上げる1 commitを作る' \
		'make board                            人間向けボードを開く (127.0.0.1:8765)' \
		'make scout-start / scout-stop         scoutの定期実行を開始・停止する（first-benchが初回baseline後に開始する）' \
		'make scout-status                     scoutの状態を表示する' \
		'make test                             問題と実サーバーを使わないテストを実行する'

discover:
	@./scripts/discover.sh

kickoff:
	@./scripts/kickoff.sh

lanes:
	@python3 scripts/lane.py status

builder-images:
	@python3 scripts/local-build.py prepare-images

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

tare:
	@python3 scripts/tare.py

bench-check:
	@./scripts/bench-check.sh

first-bench:
	@./scripts/first-bench.sh

.PHONY: board scout-start scout-stop scout-status test
board:
	@python3 scripts/board/main.py

scout-start:
	@python3 scripts/scout/main.py start

scout-stop:
	@python3 scripts/scout/main.py stop

scout-status:
	@python3 scripts/scout/main.py status

test:
	@python3 -m unittest discover -s tests -p 'test_*.py' -v

.PHONY: local-up local-check local-down local-logs local-reset local-state-log local-flow
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
	@python3 scripts/local-flow.py $(FLOW) $(if $(FROM),--continue $(FROM))

.PHONY: preparation-check
preparation-check:
	@python3 scripts/preparation-check.py

.PHONY: practice-check practice-create practice-status practice-delete
practice-check practice-create practice-status practice-delete:
	@bash scripts/practice-stack.sh $(patsubst practice-%,%,$@)
