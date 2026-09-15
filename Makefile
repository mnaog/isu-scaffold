.PHONY: help repo-init ansible-install discover ssh-bootstrap bootstrap inspect configure-draft configure-apply sync-check import language-set kickoff-code kickoff-code-ready kickoff-draft kickoff-apply kickoff-ready deploy rollback status benchmark-check benchmark-probe phase1-check test isuscope-doctor survey isuscope-run routes-suggest isuscope-pin

help:
	@printf '%s\n' \
		'make repo-init REPO=<name>            private GitHub repositoryを作成する' \
		'make ansible-install                  local venvへAnsibleを導入する' \
		'make discover                         providerからnodeと全設定を再生成する' \
		'make ssh-bootstrap                    operator SSH鍵を全nodeへ登録する' \
		'make bootstrap                        discoverからAnsible初期収束まで行う' \
		'make inspect                          全nodeの初期構成を.localへ調査する' \
		'make configure-draft                  inspectionから設定候補を生成する' \
		'CONFIRM_DRAFT=true make configure-apply  確認済みdraftを設定へ反映する' \
		'make sync-check                       import/deploy manifestを検査する' \
		'make import                           remoteの初期状態をlocalへ回収する' \
		'make language-set LANGUAGE=<name>     採用言語とwebapp内のpathを固定する' \
		'make kickoff-code LANGUAGE=<name>     SSH確立後にコードとschemaだけ先行回収する' \
		'make kickoff-code-ready               commit済みコードから並行worktreeを作る' \
		'make kickoff-draft                    discoverから設定draft生成まで進める' \
		'CONFIRM_DRAFT=true make kickoff-apply LANGUAGE=<name>  draft反映・import・言語固定まで進める' \
		'make kickoff-ready                    コード読解worktreeを作りベンチ前gateを通す' \
		'make deploy                           commit済みのlocal状態を全nodeへ反映する' \
		'make rollback RELEASE=<id>            deploy前のremote状態へ戻す' \
		'make status                           全application nodeを検査する' \
		'make benchmark-check                  ベンチを起動せずadapter設定を検査する' \
		'make benchmark-probe                  ベンチを起動せず接続先を疎通確認する' \
		'make phase1-check                     ベンチ前の全node・isuscope検査を行う' \
		'make test                             local fixtureで生成とadapterを検査する' \
		'make isuscope-doctor                  isuscope設定と接続を検査する' \
		'make survey HYPOTHESIS="..."          初回survey-runを明示的に実行する' \
		'make isuscope-run HYPOTHESIS="..."    operation lock下で通常runを実行する' \
		'make routes-suggest RUN=latest        runからroute正規化候補を生成する' \
		'make isuscope-pin RUN=<run-id>        重要なrunの生ログをGitへstageする'

repo-init:
	@test -n "$(REPO)" || { echo 'REPO=<name>を指定してください' >&2; exit 2; }
	@./scripts/repo-init.sh "$(REPO)"

ansible-install:
	@./scripts/ansible-install.sh

discover:
	@./scripts/discover.sh

ssh-bootstrap:
	@./scripts/bootstrap-ssh.sh

bootstrap:
	@./scripts/bootstrap.sh

inspect:
	@./scripts/inspect-environment.sh

configure-draft:
	@./scripts/configure-draft.sh

configure-apply:
	@./scripts/configure-apply.sh

sync-check:
	@./scripts/sync-check.sh

import:
	@./scripts/import.sh

language-set:
	@test -n "$(LANGUAGE)" || { echo 'LANGUAGE=<name>を指定してください' >&2; exit 2; }
	@./scripts/set-application-language.sh "$(LANGUAGE)" "$(APPLICATION_PATH)"

kickoff-code:
	@test -n "$(LANGUAGE)" || { echo 'LANGUAGE=<name>を指定してください' >&2; exit 2; }
	@./scripts/kickoff-code.sh "$(LANGUAGE)" "$(APPLICATION_PATH)"

kickoff-code-ready:
	@./scripts/kickoff-code-ready.sh

kickoff-draft:
	@./scripts/kickoff-draft.sh

kickoff-apply:
	@test -n "$(LANGUAGE)" || { echo 'LANGUAGE=<name>を指定してください' >&2; exit 2; }
	@./scripts/kickoff-apply.sh "$(LANGUAGE)" "$(APPLICATION_PATH)"

kickoff-ready:
	@./scripts/kickoff-ready.sh

deploy:
	@./scripts/deploy.sh

rollback:
	@test -n "$(RELEASE)" || { echo 'RELEASE=<id>を指定してください' >&2; exit 2; }
	@./scripts/rollback.sh "$(RELEASE)"

status:
	@./scripts/status.sh

benchmark-check:
	@./scripts/benchmark-check.sh

benchmark-probe:
	@./scripts/benchmark-probe.sh

phase1-check:
	@./scripts/phase1-check.sh

test:
	@./tests/initial-automation.sh

isuscope-doctor:
	@isuscope doctor

survey:
	@test -n "$(HYPOTHESIS)" || { echo 'HYPOTHESIS="..."を指定してください' >&2; exit 2; }
	@./scripts/with-operation-lock.sh isuscope survey-run --hypothesis "$(HYPOTHESIS)"

isuscope-run:
	@test -n "$(HYPOTHESIS)" || { echo 'HYPOTHESIS="..."を指定してください' >&2; exit 2; }
	@./scripts/with-operation-lock.sh isuscope run --hypothesis "$(HYPOTHESIS)"

routes-suggest:
	@./scripts/suggest-routes.sh "$(or $(RUN),latest)"

isuscope-pin:
	@test -n "$(RUN)" || { echo 'RUN=<run-id> を指定してください' >&2; exit 2; }
	@./scripts/isuscope-pin.sh "$(RUN)"
