"""Prompt 18 tests — plate photos chained to plate events."""
import os

import pytest

from modules.dish_counter import DishCounter
from modules.pass_photo import (MockPlateAnalyzer, PassPhoto, PlateAnalyzer,
                                RealPlateAnalyzer)


@pytest.fixture
def photos(config, db, bus):
    counter = DishCounter(config, db, bus)
    return PassPhoto(config, db, bus, dish_counter=counter), counter


def test_mock_analyzer_reads_metric_tokens():
    analyzer = MockPlateAnalyzer()
    metrics = analyzer.score("/tmp/plate_sym0.8_por0.9_garn0.7.jpg")
    assert metrics == {"symmetry": 0.8, "portion": 0.9, "garnish": 0.7}
    defaults = analyzer.score("/tmp/IMG_4821.jpg")
    assert defaults["symmetry"] == 0.85


def test_real_analyzer_is_an_explicit_extension_point():
    with pytest.raises(NotImplementedError):
        RealPlateAnalyzer().score("/tmp/x.jpg")
    assert issubclass(MockPlateAnalyzer, PlateAnalyzer)


def test_capture_writes_a_file_and_a_row(photos, db):
    book, counter = photos
    counter.fire("r-1")
    counter.plated("r-1")
    result = book.capture("r-1")
    assert os.path.exists(result["path"])
    assert result["path"].startswith(book.config.photo_dir)
    row = db.query_one("SELECT * FROM dish_photos WHERE id = ?", (result["photo_id"],))
    assert row["dish_id"] == "r-1"
    assert row["pending_review"] == 1
    assert "symmetry" in row["metrics"]


def test_capture_links_to_the_plated_event(photos):
    book, counter = photos
    counter.fire("r-1")
    event = counter.plated("r-1")
    result = book.capture("r-1")
    assert result["event_id"] == event.id


def test_capture_without_a_plated_event_still_records_the_photo(photos):
    book, counter = photos
    result = book.capture("r-unknown")
    assert result["event_id"] is None
    assert os.path.exists(result["path"])


def test_second_plate_gets_a_second_file(photos):
    book, counter = photos
    first = book.capture("r-1")
    second = book.capture("r-1")
    assert first["path"] != second["path"]
    assert second["path"].endswith("_02.jpg")


def test_gallery_filters_by_date_and_dish(photos):
    book, counter = photos
    book.capture("r-1", service_date="2026-10-01")
    book.capture("r-2", service_date="2026-10-01")
    book.capture("r-1", service_date="2026-10-02")
    assert len(book.gallery("2026-10-01")) == 2
    assert len(book.gallery(dish_id="r-1")) == 2
    assert len(book.gallery("2026-10-02", "r-1")) == 1


def test_unreviewed_queue_and_marking(photos):
    book, counter = photos
    first = book.capture("r-1")
    book.capture("r-1")
    assert len(book.unreviewed()) == 2
    book.mark_reviewed(first["photo_id"])
    remaining = book.unreviewed()
    assert len(remaining) == 1
    assert remaining[0]["id"] != first["photo_id"]


def test_compare_returns_both_dates(photos):
    book, counter = photos
    book.capture("r-1", service_date="2026-09-30")
    book.capture("r-1", service_date="2026-10-01")
    comparison = book.compare("r-1", "2026-09-30", "2026-10-01")
    assert comparison["a"]["service_date"] == "2026-09-30"
    assert comparison["b"]["service_date"] == "2026-10-01"
    assert comparison["a"]["path"] != comparison["b"]["path"]


def test_hostile_dish_id_cannot_escape_the_photo_store(photos):
    book, counter = photos
    result = book.capture("../../etc/passwd")
    assert result["path"].startswith(book.config.photo_dir)
    assert ".." not in os.path.basename(result["path"])
    assert os.path.exists(result["path"])
    # the database still records what the kitchen actually called the dish
    assert book.get(result["photo_id"])["dish_id"] == "../../etc/passwd"


def test_no_blob_is_stored_in_the_database(photos, db):
    book, counter = photos
    book.capture("r-1")
    row = db.query_one("SELECT * FROM dish_photos")
    assert "placeholder-plate-photo" not in str(dict(row))


def test_storage_usage_counts_files(photos):
    book, counter = photos
    book.capture("r-1")
    usage = book.storage_usage()
    assert usage["photos"] == 1
    assert usage["bytes"] > 0
    assert usage["missing_files"] == 0
