// Supplemental operator audit, designed after seeing the completed Codex diff.
// Checks byte-for-byte array order against the current DB path, not a claim
// that SQL without ORDER BY guarantees this order on every server/version.
#[actix_web::test]
#[ignore = "requires disposable local MySQL"]
async fn medium_ordering_audit() {
    let h = Harness::new().await;
    h.database.control().execute("INSERT INTO item_masters VALUES (5,3,'higher material','',NULL,NULL,NULL,NULL,100,NULL),(8,2,'higher card','',1,50,50,10,NULL,NULL)").await.unwrap();
    h.database.control().execute("UPDATE login_bonus_reward_masters SET item_id=5 WHERE id=3").await.unwrap();
    h.database.control().execute("UPDATE login_bonus_reward_masters SET item_id=8 WHERE id=2").await.unwrap();
    h.masters.reload(h.database.control()).await.unwrap();
    let created = h.create().await;
    h.assert_matches_db(created.user_id).await;
    let ids = created.updated_resources.user_presents.as_ref().unwrap().iter().map(|p| p.id).collect();
    h.receive(created.user_id, ids).await.unwrap();
    let cached = h.items(created.user_id, h.cache.clone()).await;
    let database = h.items(created.user_id, web::Data::new(NewUserCache::default())).await;
    println!("ORDER cached cards={:?} items={:?}", cached.cards.iter().map(|c| c.card_id).collect::<Vec<_>>(), cached.items.iter().map(|i| i.item_id).collect::<Vec<_>>());
    println!("ORDER db cards={:?} items={:?}", database.cards.iter().map(|c| c.card_id).collect::<Vec<_>>(), database.items.iter().map(|i| i.item_id).collect::<Vec<_>>());
    h.assert_matches_db(created.user_id).await;
}
