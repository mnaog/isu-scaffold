# reviewer

役割: reviewer。`SCAFFOLD_ROLE=reviewer`。正式公開されたresearcher提案の全件にCodexとClaude Codeがそれぞれ独立セッションでレビューする。scout短報、worker記録、operator発言、調査メモは対象外。重要度や採否による選別はしない。

[起動プロンプト](reviewer-prompt.txt)とAGENTS.mdの練習ルールに従う。共有アプリ・設定の変更、remote操作、deploy、ベンチは担当しない。自身のレビュー・実行状態だけを保存できる（非対話起動ではscaffoldが保存する）。読み取り専用の指示はプロンプトによる契約であり、OS sandboxによる保証ではない。

元の問いと固定版の根拠をコード・計測と照合する。対象commitは`git show <commit>:<path>`などで確認し、現在のworktreeとの差は区別する。runも指定IDを使い、latestで置き換えない。未コミット根拠はsnapshotと対象commitの差を確認する。参照できない情報は未確認とし、条件・反例・因果関係を重視する。

最終回答はJSONの`summary`、`details`、`proposal_sha256`。summaryは判断を変える情報を短く先頭に置く。CLI exit 0だけでは完了にならず、対象hashと必須項目の確認、成果物ファイルとSQLiteへの保存が必要。レビュー結果は到着したものから公開される。operatorの採用・作業開始・読む順番は制約しない。

起動・停止・再試行は[researcher運用](researcher.md)と共通。reviewerはoperatorとして登録せず、worker記録にも書き込まない。
