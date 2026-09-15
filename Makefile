.PHONY: help discover kickoff kickoff-apply deploy rollback status phase1-check

help:
	@printf '%s\n' \
		'make discover                         providerからnodeと全設定を再生成する' \
		'make kickoff                          コード先行回収・worktree作成からdraft生成と検査まで進める' \
		'CONFIRM_DRAFT=true make kickoff-apply  検査済みdraftを反映して完全importする' \
		'make deploy                           commit済みのlocal状態を全nodeへ反映する' \
		'make rollback RELEASE=<id>            deploy前のremote状態へ戻す' \
		'make status                           全application nodeを検査する' \
		'make phase1-check                     ベンチ前の全node・isuscope検査を行う'

discover:
	@./scripts/discover.sh

kickoff:
	@./scripts/kickoff.sh

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
