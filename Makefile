.PHONY: help discover kickoff kickoff-apply build deploy rollback status phase1-check tare lanes builder-images

help:
	@printf '%s\n' \
		'make discover                         providerからnodeと全設定を再生成する' \
		'make kickoff                          コード先行回収・初期buildからdraft生成と検査まで進める' \
		'make lanes                            claude -pのAI lane（code-map、obvious-fixesなど）の状態を見る' \
		'make worktree BRANCH=<name> PURPOSE="..."  目的つきで並行laneのworktreeを作る' \
		'CONFIRM_DRAFT=true make kickoff-apply  検査済みdraftを反映して完全importする' \
		'make local-up / local-check / local-down  worktree専用のローカル実行環境を操作する' \
		'make build                            ローカルでLinux向けRust成果物を準備する' \
		'make builder-images                   Phase 0: 候補baseのRust builder imageを事前に作る' \
		'make deploy                           commit済みのlocal状態を全nodeへ反映する' \
		'make rollback RELEASE=<id>            deploy前のremote状態へ戻す' \
		'make status                           全application nodeを検査する' \
		'make phase1-check                     ベンチ前の全node・isuscope検査を行う' \
		'make tare                             baseline後に接続・fdの上限を上げる1 commitを作る' \
		'make board                            人間向けボードを開く (127.0.0.1:8765)' \
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

phase1-check:
	@./scripts/phase1-check.sh

.PHONY: board test
board:
	@python3 scripts/board/main.py

test:
	@python3 -m unittest discover -s tests -p 'test_*.py' -v

.PHONY: local-up local-check local-down local-logs local-reset
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

.PHONY: phase0-check
phase0-check:
	@python3 scripts/check-phase0.py

.PHONY: practice-check practice-create practice-status practice-delete
practice-check practice-create practice-status practice-delete:
	@bash scripts/practice-stack.sh $(patsubst practice-%,%,$@)
