// Operator-owned, visible acceptance checks. Included inside the existing integration module.
async fn medium_selects(h: &Harness) -> u64 {
    let row: (String, String) = sqlx::query_as("SHOW GLOBAL STATUS LIKE 'Com_select'")
        .fetch_one(h.database.control()).await.unwrap();
    row.1.parse().unwrap()
}

async fn medium_inventory(h: &Harness, id: i64, phase: &str) -> ListItemResponse {
    let before = medium_selects(h).await;
    let result = h.items(id, h.cache.clone()).await;
    let reads = medium_selects(h).await - before;
    println!("SELECTS {} {}", phase, reads);
    assert_eq!(reads, 0, "inventory must remain complete: {}", phase);
    result
}

#[actix_web::test]
#[ignore = "requires disposable local MySQL"]
async fn medium_acceptance() {
    let h = Harness::new().await;
    // A material that is not owned at registration, granted together with an existing one.
    h.database.control().execute("INSERT INTO item_masters VALUES (4,3,'new material','',NULL,NULL,NULL,NULL,100,NULL)").await.unwrap();
    h.database.control().execute("INSERT INTO present_all_masters VALUES (4,0,2000000000,3,4,7,'new material',0)").await.unwrap();
    h.masters.reload(h.database.control()).await.unwrap();
    let before = medium_selects(&h).await;
    let created = h.create().await;
    let registration_reads = medium_selects(&h).await - before;
    println!("SELECTS registration {}", registration_reads);
    assert_eq!(registration_reads, 0, "do not add readback queries to registration");
    assert_eq!(created.updated_resources.user_cards.as_ref().unwrap().len(), 3,
               "registration response remains partial, with only initial cards");
    assert!(created.updated_resources.user_items.is_none());
    let id = created.user_id;
    let inventory = medium_inventory(&h, id, "after_registration").await;
    assert_eq!(inventory.cards.len(), 4);
    assert_eq!(inventory.items.len(), 1);
    assert_eq!(inventory.items[0].amount, 5);
    h.assert_matches_db(id).await;

    let ids: Vec<_> = created.updated_resources.user_presents.as_ref().unwrap().iter().map(|p| p.id).collect();
    let mut duplicated = ids.clone();
    duplicated.extend(ids.clone());
    let before = medium_selects(&h).await;
    h.receive(id, duplicated).await.unwrap();
    let reads = medium_selects(&h).await - before;
    println!("SELECTS receive_known {}", reads);
    assert_eq!(reads, 0, "known inventory receipt requires no SELECT");
    let inventory = medium_inventory(&h, id, "after_receipt").await;
    assert_eq!(inventory.cards.len(), 5);
    assert_eq!(inventory.items.len(), 2);
    assert_eq!(inventory.items.iter().find(|i| i.item_id == 3).unwrap().amount, 10);
    assert_eq!(inventory.items.iter().find(|i| i.item_id == 4).unwrap().amount, 7);
    assert_eq!(inventory.user.isu_coin, 50100);
    h.assert_matches_db(id).await;
    h.receive(id, ids).await.unwrap();
    medium_inventory(&h, id, "after_duplicate_receipt").await;
    h.assert_matches_db(id).await;

    // No bonus cards/materials is still a complete inventory, including known-empty items.
    h.database.control().execute("DELETE FROM login_bonus_masters WHERE id IN (2,3)").await.unwrap();
    h.masters.reload(h.database.control()).await.unwrap();
    let empty = h.create().await;
    let inv = medium_inventory(&h, empty.user_id, "known_empty_items").await;
    assert!(inv.items.is_empty());
    let ids = empty.updated_resources.user_presents.as_ref().unwrap().iter().map(|p| p.id).collect();
    let before = medium_selects(&h).await;
    h.receive(empty.user_id, ids).await.unwrap();
    assert_eq!(medium_selects(&h).await - before, 0);
    medium_inventory(&h, empty.user_id, "after_first_material").await;
    h.assert_matches_db(empty.user_id).await;

    // Reset invalidates the optimization but persistent results must stay identical.
    h.cache.clear();
    h.assert_matches_db(id).await;
    h.assert_matches_db(empty.user_id).await;
}
