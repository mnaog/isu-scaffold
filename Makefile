.PHONY: help discover kickoff-code kickoff-code-ready kickoff-draft kickoff-apply deploy rollback status phase1-check test

help:
	@printf '%s\n' \
		'make discover                         providerからnodeと全設定を再生成する' \
		'make kickoff-code LANGUAGE=<name>     SSH確立後にコードとschemaだけ先行回収する' \
		'make kickoff-code-ready               commit済みコードから並行worktreeを作る' \
		'make kickoff-draft                    discoverから設定draft生成まで進める' \
		'CONFIRM_DRAFT=true make kickoff-apply LANGUAGE=<name>  draft反映・import・言語固定まで進める' \
		'make deploy                           commit済みのlocal状態を全nodeへ反映する' \
		'make rollback RELEASE=<id>            deploy前のremote状態へ戻す' \
		'make status                           全application nodeを検査する' \
		'make phase1-check                     ベンチ前の全node・isuscope検査を行う' \
		'make test                             local fixtureで生成とadapterを検査する'

discover:
	@./scripts/discover.sh

kickoff-code:
	@test -n "$(LANGUAGE)" || { echo 'LANGUAGE=<name>を指定してください' >&2; exit 2; }
	@./scripts/kickoff-code.sh "$(LANGUAGE)" "$(APPLICATION_PATH)"

kickoff-code-ready:
	@./scripts/create-phase1-worktree.sh
	@echo 'start code and schema review in the phase1 worktree while main runs make kickoff-draft'

kickoff-draft:
	@./scripts/kickoff-draft.sh

kickoff-apply:
	@test -n "$(LANGUAGE)" || { echo 'LANGUAGE=<name>を指定してください' >&2; exit 2; }
	@./scripts/kickoff-apply.sh "$(LANGUAGE)" "$(APPLICATION_PATH)"

deploy:
	@./scripts/deploy.sh

rollback:
	@test -n "$(RELEASE)" || { echo 'RELEASE=<id>を指定してください' >&2; exit 2; }
	@./scripts/rollback.sh "$(RELEASE)"

status:
	@./scripts/status.sh

phase1-check:
	@./scripts/phase1-check.sh

test:
	@./tests/initial-automation.sh

