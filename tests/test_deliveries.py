"""Prompt 09 tests — delay history, p90 planning delay, grades, CSV import."""
import pytest

from modules.deliveries import Deliveries


@pytest.fixture
def log(config, db, bus):
    return Deliveries(config, db, bus)


@pytest.fixture
def supplier(log):
    return log.add_supplier("Green Ridge Farms", order_cutoff="07:30",
                            typical_delay_days=1.0, closed_days=[6])


def test_supplier_round_trip(log, supplier):
    fetched = log.get_supplier(supplier.id)
    assert fetched.name == "Green Ridge Farms"
    assert fetched.order_cutoff == "07:30"
    assert fetched.closed_days == [6]
    assert len(log.suppliers()) == 1


def test_record_arrival_computes_delay(log, supplier):
    record = log.record_arrival(supplier.id, "2026-09-28", "2026-09-29", "2026-10-01")
    assert record.delay_days == 2.0
    early = log.record_arrival(supplier.id, "2026-09-20", "2026-09-22", "2026-09-21")
    assert early.delay_days == -1.0


def test_median_and_p90_on_known_history(log, supplier):
    delays = [0, 0, 1, 1, 1, 2, 2, 3, 3, 5]
    for i, delay in enumerate(delays):
        promised_day = 1 + i
        actual_day = promised_day + delay
        log.record_arrival(supplier.id, f"2026-09-{promised_day:02d}",
                           f"2026-09-{promised_day:02d}",
                           f"2026-09-{actual_day:02d}")
    assert log.median_delay(supplier.id) == 1.5
    # sorted delays: index ceil(0.9*10)-1 = 8 -> 3 days
    assert log.p90_delay(supplier.id) == 3.0


def test_p90_is_the_nine_in_ten_number_and_worst_recent_shows_the_disaster(log, supplier):
    for delay in [0, 0, 0, 0, 0, 0, 0, 0, 0, 6]:
        log.record_arrival(supplier.id, "2026-09-01", "2026-09-02",
                           f"2026-09-{2 + delay:02d}")
    # nine on-time drops and one disaster: p90 says 0, and that is honest —
    # 9 deliveries in 10 beat it. The disaster is reported separately.
    assert log.median_delay(supplier.id) == 0.0
    assert log.p90_delay(supplier.id) == 0.0
    assert log.worst_recent_delay(supplier.id) == 6.0
    # a chef who wants slack can buy it explicitly
    assert log.planning_lead(supplier.id, safety_buffer_days=2.0) == 2.0


def test_empty_history_uses_configured_default(log, supplier):
    assert log.median_delay(supplier.id) == 1.0
    assert log.p90_delay(supplier.id) == 1.0
    grade = log.reliability_grade(supplier.id)
    assert grade["grade"] == "?"
    assert "no delivery history" in grade["rationale"]


def test_unknown_supplier_defaults_to_one_day(log):
    assert log.p90_delay("does-not-exist") == 1.0
    assert log.get_supplier("does-not-exist") is None


def test_reliability_grades(log):
    reliable = log.add_supplier("Prompt Produce", typical_delay_days=0.0)
    for day in range(1, 11):
        log.record_arrival(reliable.id, f"2026-09-{day:02d}", f"2026-09-{day:02d}",
                           f"2026-09-{day:02d}")
    assert log.reliability_grade(reliable.id)["grade"] == "A"

    sloppy = log.add_supplier("Sloppy Supply")
    for day in range(1, 11):
        log.record_arrival(sloppy.id, f"2026-09-{day:02d}", f"2026-09-{day:02d}",
                           f"2026-09-{day + 3:02d}")
    grade = log.reliability_grade(sloppy.id)
    assert grade["grade"] == "D"
    assert grade["deliveries"] == 10
    assert "mean delay 3.00d" in grade["rationale"]


def test_on_time_rate(log, supplier):
    log.record_arrival(supplier.id, "2026-09-01", "2026-09-02", "2026-09-02")
    log.record_arrival(supplier.id, "2026-09-02", "2026-09-03", "2026-09-05")
    assert log.on_time_rate(supplier.id) == 0.5


def test_csv_import(log, supplier, tmp_path):
    path = tmp_path / "delays.csv"
    path.write_text(
        "supplier_id,ordered_date,promised_date,actual_date\n"
        f"{supplier.id},2026-09-01,2026-09-02,2026-09-03\n"
        f"{supplier.id},2026-09-03,2026-09-04,2026-09-04\n"
        ",2026-09-05,2026-09-06,2026-09-07\n",  # incomplete row is skipped
        encoding="utf-8")
    assert log.import_csv(str(path)) == 2
    assert len(log.delay_history(supplier.id)) == 2


def test_supplier_report_has_grades_and_delays(log, supplier):
    log.record_arrival(supplier.id, "2026-09-01", "2026-09-02", "2026-09-04")
    report = log.supplier_report()
    assert len(report) == 1
    assert report[0]["name"] == "Green Ridge Farms"
    assert report[0]["p90_delay"] == 2.0
    assert report[0]["grade"] in ("A", "B", "C", "D")
