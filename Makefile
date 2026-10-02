.PHONY: help discover kickoff kickoff-apply deploy rollback status phase1-check

help:
	@printf '%s\n' \
		'make discover                         providerからnodeと全設定を再生成する' \
		'make kickoff                          コード先行回収・worktree作成からdraft生成と検査まで進める' \
		'make worktree BRANCH=<name> PURPOSE="..."  目的つきで並行laneのworktreeを作る' \
		'CONFIRM_DRAFT=true make kickoff-apply  検査済みdraftを反映して完全importする' \
		'make deploy                           commit済みのlocal状態を全nodeへ反映する' \
		'make rollback RELEASE=<id>            deploy前のremote状態へ戻す' \
		'make status                           全application nodeを検査する' \
		'make phase1-check                     ベンチ前の全node・isuscope検査を行う' \
		'make operations-init                  共通SQLiteと最新scout boardを初期化する' \
		'make board                            人間向けボードを開く (127.0.0.1:8765)' \
		'make scout-start / scout-stop         人間の判断でscout定期実行を開始・停止する' \
		'make scout-status                     worker・scoutの状態を表示する' \
		'make operations-test                  ローカル運用の偽CLIテストを実行する'

discover:
	@./scripts/discover.sh

kickoff:
	@./scripts/kickoff.sh

worktree:
	@test -n "$(BRANCH)" || { echo 'BRANCH=<name>を指定してください' >&2; exit 2; }
	@test -n "$(PURPOSE)" || { echo 'PURPOSE="何をするlaneか"を指定してください' >&2; exit 2; }
	@./scripts/worktree.sh "$(BRANCH)" "$(PURPOSE)" "$(or $(BASE),main)"

kickoff-apply:
	@./scripts/kickoff-apply.sh

deploy:
	@./scripts/deploy.sh

rollback:
	@test -n "$(RELEASE)" || { echo 'RELEASE=<id>を指定してください' >&2; exit 2; }
	@./scripts/rollback.sh "$(RELEASE)"

status:
	@./scripts/status.sh

phase1-check:
	@./scripts/phase1-check.sh

.PHONY: operations-init scout-start scout-stop scout-status board operations-test
operations-init:
	@./scripts/scout init

scout-start:
	@./scripts/scout start

scout-stop:
	@./scripts/scout stop

scout-status:
	@./scripts/scout status

board:
	@./scripts/scout board

operations-test:
	@python3 -m unittest discover -s tests -p 'test_*.py' -v
