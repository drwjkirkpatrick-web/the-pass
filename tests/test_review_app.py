"""Prompt 19 tests — live scoring logic (and the Flask shell when present)."""
import pytest

from core.events import DISH_REVIEWED
from modules.dish_counter import DishCounter
from modules.pass_photo import PassPhoto
from modules.review_app import SCORE_FIELDS, ReviewLogic


@pytest.fixture
def review(config, db, bus):
    counter = DishCounter(config, db, bus)
    photos = PassPhoto(config, db, bus, dish_counter=counter)
    logic = ReviewLogic(config, db, bus, pass_photo=photos, dish_counter=counter)
    return logic, photos, counter


def test_next_pending_is_oldest_first(review):
    logic, photos, counter = review
    first = photos.capture("r-1")
    photos.capture("r-2")
    assert logic.next_pending()["id"] == first["photo_id"]
    logic.record_score(first["photo_id"], {"presentation": 5, "portion": 4,
                                           "color": 5, "execution": 5})
    assert logic.next_pending()["dish_id"] == "r-2"


def test_next_pending_when_queue_is_empty(review):
    logic, photos, counter = review
    assert logic.next_pending() is None


def test_record_score_writes_review_clears_queue_and_publishes(review, bus, db):
    logic, photos, counter = review
    captured = photos.capture("r-1")
    seen = []
    bus.subscribe(DISH_REVIEWED, lambda t, p: seen.append(p))

    review_row = logic.record_score(captured["photo_id"],
                                    {"presentation": 5, "portion": 3, "color": 4,
                                     "execution": 5}, note="sauce broke slightly")

    assert review_row.presentation == 5
    assert review_row.portion == 3
    assert review_row.note == "sauce broke slightly"
    assert seen and seen[0]["id"] == review_row.id
    assert db.query_one("SELECT pending_review FROM dish_photos WHERE id = ?",
                        (captured["photo_id"],))["pending_review"] == 0


def test_scoring_an_unknown_photo_raises(review):
    logic, photos, counter = review
    with pytest.raises(KeyError):
        logic.record_score("ph-nope", {"presentation": 5})


def test_average_scores_and_trend(review):
    logic, photos, counter = review
    for day, value in (("2026-09-30", 5), ("2026-10-01", 3)):
        photo = photos.capture("r-1", service_date=day)
        logic.record_score(photo["photo_id"], {f: value for f in SCORE_FIELDS})
    average = logic.average_scores(dish_id="r-1")
    assert average["n"] == 2
    assert average["overall"] == 4.0

    trend = logic.trend("r-1")
    assert [t["service_date"] for t in trend] == ["2026-09-30", "2026-10-01"]
    assert trend[0]["overall"] == 5.0
    assert trend[1]["overall"] == 3.0


def test_live_strip_shows_scores_and_pace(review):
    logic, photos, counter = review
    photo = photos.capture("r-1")
    counter.plated("r-1")
    logic.record_score(photo["photo_id"], {"presentation": 4, "portion": 4,
                                           "color": 4, "execution": 4})
    strip = logic.live()
    assert strip[0]["dish_id"] == "r-1"
    assert strip[0]["score"] == 4.0
    assert "Pace:" in logic.html_live()


def test_html_escapes_user_supplied_notes(review):
    logic, photos, counter = review
    photo = photos.capture("<script>alert('x')</script>")
    page = logic.html_next()
    assert "&lt;script&gt;" in page
    assert "<script>alert" not in page


def test_html_pages_render_without_data(review):
    logic, photos, counter = review
    assert "No plates waiting" in logic.html_next()
    assert "Not enough history" in logic.html_trend("r-1")


def test_flask_shell_serves_the_routes_when_flask_is_installed(review):
    flask = pytest.importorskip("flask")
    logic, photos, counter = review
    captured = photos.capture("r-1")
    app = __import__("modules.review_app", fromlist=["create_app"]).create_app(logic)
    client = app.test_client()

    assert client.get("/review/next").status_code == 200
    assert client.get("/review/live").status_code == 200
    assert client.get("/review/trend?dish=r-1").status_code == 200

    response = client.post("/review/score", data={
        "photo_id": captured["photo_id"], "presentation": 5, "portion": 5,
        "color": 5, "execution": 5, "note": "textbook"})
    assert response.status_code == 201
    assert logic.average_scores(dish_id="r-1")["overall"] == 5.0

    bad = client.post("/review/score", data={"presentation": 5})
    assert bad.status_code == 400
