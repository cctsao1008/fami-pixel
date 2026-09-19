from fami_pixel.planning import shard_unique_items


def test_unique_worker_sharding_matches_modulo_partition_contract():
    items = tuple(range(8))

    assert shard_unique_items(items, worker_index=0, worker_count=3) == (0, 3, 6)
    assert shard_unique_items(items, worker_index=1, worker_count=3) == (1, 4, 7)
    assert shard_unique_items(items, worker_index=2, worker_count=3) == (2, 5)


def test_surplus_workers_receive_no_duplicate_items():
    items = tuple(range(8))
    shards = [
        shard_unique_items(items, worker_index=worker, worker_count=12)
        for worker in range(12)
    ]

    flattened = [item for shard in shards for item in shard]
    assert flattened == list(items)
    assert shards[8:] == [(), (), (), ()]


def test_non_positive_worker_count_preserves_historical_single_worker_normalization():
    items = ("a", "b", "c")
    assert shard_unique_items(items, worker_index=0, worker_count=0) == items
    assert shard_unique_items(items, worker_index=1, worker_count=0) == ()
