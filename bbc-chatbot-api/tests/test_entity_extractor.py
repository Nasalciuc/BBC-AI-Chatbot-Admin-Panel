"""Tests for entity extraction V1 — regex-based."""

import pytest
from freezegun import freeze_time
from app.pipeline.entity_extractor import extract_entities, extract_kb_keywords


class TestEmailExtraction:
    def test_simple_email(self):
        e = extract_entities("my email is john@example.com")
        assert e.email == "john@example.com"

    def test_email_in_sentence(self):
        e = extract_entities("send quote to sarah.miller@gmail.com thanks")
        assert e.email == "sarah.miller@gmail.com"

    def test_no_email(self):
        e = extract_entities("I want to fly to London")
        assert e.email is None

    def test_corporate_email(self):
        e = extract_entities("contact me at mike.chen@corp.com")
        assert e.email == "mike.chen@corp.com"


class TestPhoneExtraction:
    def test_us_format_dashes(self):
        e = extract_entities("call me at 212-555-0187")
        assert e.phone is not None
        assert "212" in e.phone

    def test_us_format_plus(self):
        e = extract_entities("phone +1-310-555-0293")
        assert e.phone is not None

    def test_no_phone(self):
        e = extract_entities("I need a flight to Paris")
        assert e.phone is None


class TestAirportCodes:
    def test_two_codes(self):
        e = extract_entities("I need JFK to LHR flights")
        assert e.origin_code == "JFK"
        assert e.destination_code == "LHR"

    def test_three_codes_takes_first_two(self):
        e = extract_entities("flying JFK to LHR via CDG")
        assert e.origin_code == "JFK"
        assert e.destination_code == "LHR"

    def test_single_code(self):
        e = extract_entities("I want to go to DXB")
        assert e.destination_code == "DXB"


class TestCityNames:
    def test_new_york_to_london(self):
        e = extract_entities("from New York to London")
        assert e.origin_code == "JFK"
        assert e.destination_code == "LHR"

    def test_miami_to_paris(self):
        e = extract_entities("from Miami to Paris please")
        assert e.origin_code == "MIA"
        assert e.destination_code == "CDG"

    def test_unknown_city(self):
        e = extract_entities("from Timbuktu to Mars")
        assert e.origin_code is None


class TestNameExtraction:
    def test_my_name_is(self):
        e = extract_entities("My name is John Thompson")
        assert e.name == "John Thompson"

    def test_im(self):
        e = extract_entities("Hi, I'm Sarah")
        assert e.name == "Sarah"

    def test_no_name(self):
        e = extract_entities("I want a flight")
        assert e.name is None


class TestPassengers:
    def test_explicit(self):
        e = extract_entities("2 passengers business class")
        assert e.passengers == 2

    def test_just_me(self):
        e = extract_entities("just me flying business")
        assert e.passengers == 1

    def test_two_of_us(self):
        e = extract_entities("two of us going to Rome")
        assert e.passengers == 2


class TestCabinClass:
    def test_business(self):
        e = extract_entities("I want business class to London")
        assert e.cabin_class == "business"

    def test_first(self):
        e = extract_entities("first class JFK to DXB")
        assert e.cabin_class == "first"


class TestCombined:
    def test_full_message(self):
        e = extract_entities(
            "Hi, I'm John Thompson. I need business class flights "
            "from New York to London for 2 passengers. "
            "Email: john@example.com, phone +1-212-555-0187"
        )
        assert e.name == "John Thompson"
        assert e.email == "john@example.com"
        assert e.phone is not None
        assert e.origin_code == "JFK"
        assert e.destination_code == "LHR"
        assert e.passengers == 2
        assert e.cabin_class == "business"

    def test_minimal_message(self):
        e = extract_entities("hello")
        assert e.email is None
        assert e.phone is None
        assert e.name is None
        assert e.origin_code is None


class TestKBKeywords:
    def test_removes_stopwords(self):
        kw = extract_kb_keywords("I want to fly from New York to London")
        assert "want" not in kw
        assert "from" not in kw

    def test_keeps_meaningful(self):
        kw = extract_kb_keywords("business class baggage allowance London")
        assert "baggage" in kw or "allowance" in kw
        assert "london" in kw

    def test_airport_codes_boosted(self):
        kw = extract_kb_keywords("flights from JFK to LHR nonstop")
        assert kw[0] == "jfk" or kw[1] == "lhr"


@freeze_time("2026-07-20")
class TestDateExtraction:
    """Test date extraction from chat messages. Frozen at 2026-07-20 so
    assertions stay stable across real-world time (and prove the future gate)."""

    def test_month_day(self):
        # March 15 already passed in 2026 → next occurrence 2027.
        e = extract_entities("I want to fly March 15")
        assert e.departure_date == "2027-03-15"

    def test_month_abbreviated(self):
        e = extract_entities("Departing Mar 20")
        assert e.departure_date == "2027-03-20"

    def test_day_month_inverted(self):
        e = extract_entities("Leaving on 15th March")
        assert e.departure_date == "2027-03-15"

    def test_numeric_with_year(self):
        # Explicit past year 2026 (March already passed) → corrected forward.
        e = extract_entities("Flying 3/15/2026")
        assert e.departure_date == "2027-03-15"

    def test_numeric_no_year(self):
        e = extract_entities("Travel on 3/15")
        assert e.departure_date == "2027-03-15"

    def test_range_dash(self):
        e = extract_entities("March 15-22 business class")
        assert e.departure_date == "2027-03-15"
        assert e.return_date == "2027-03-22"

    def test_month_day_with_year(self):
        # June 10, 2026 already passed → corrected to next occurrence 2027.
        e = extract_entities("June 10, 2026")
        assert e.departure_date == "2027-06-10"

    def test_future_month_same_year(self):
        # August 15 is still ahead of Jul 20 → stays 2026.
        e = extract_entities("Flying August 15, 2026")
        assert e.departure_date == "2026-08-15"

    def test_future_month_no_year(self):
        e = extract_entities("Departing December 10")
        assert e.departure_date == "2026-12-10"

    def test_no_dates(self):
        e = extract_entities("I need a flight to London")
        assert e.departure_date is None
        assert e.return_date is None

    def test_ordinal(self):
        # April 3 already passed → 2027.
        e = extract_entities("Departing on the 3rd of April")
        assert e.departure_date == "2027-04-03"

    def test_numeric_ddmm_first_gt_12(self):
        # 15/03/2026 — first number 15 can't be a month → DD/MM → 15 March.
        e = extract_entities("Flying 15/03/2026")
        assert e.departure_date == "2027-03-15"

    def test_numeric_mmdd_ambiguous_unchanged(self):
        # 09/05 — both <= 12 → stays MM/DD (September 5), future in 2026.
        e = extract_entities("Travel on 09/05")
        assert e.departure_date == "2026-09-05"

    def test_combined_with_route(self):
        e = extract_entities("JFK to LHR March 15-22 business class for 2 passengers")
        assert e.origin_code == "JFK"
        assert e.destination_code == "LHR"
        assert e.departure_date == "2027-03-15"
        assert e.return_date == "2027-03-22"
        assert e.cabin_class == "business"
        assert e.passengers == 2

    def test_invalid_date_ignored(self):
        e = extract_entities("Flying on February 30")
        assert e.departure_date is None


@freeze_time("2026-11-23")
class TestRangeEndsAndMonthYear:
    """23 Nov 2026: a range whose end is only a day, written with a word ("april 5 to 26"), kept the
    departure and lost the return; "Nov. 8" (with the period) was no date; a month with its year
    ("January 2027") became the 20th, a day no client wrote. Phrasings written like the chats', none
    copied from one."""

    @pytest.mark.parametrize("text,dep,ret", [
        ("april 5 to 26", "2027-04-05", "2027-04-26"),
        ("december 15 to 27", "2026-12-15", "2026-12-27"),
        ("january 9 t0 23 round trip 2027", "2027-01-09", "2027-01-23"),
        ("Feb 3rd through the 10th", "2027-02-03", "2027-02-10"),
        ("March 28-5", "2027-03-28", "2027-04-05"),          # the end before the start: next month
        ("december 31 to 3", "2026-12-31", "2027-01-03"),
        ("November 20-28", "2027-11-20", "2027-11-28"),      # never a return a year before the departure
        ("March 15-22", "2027-03-15", "2027-03-22"),         # as before
        ("dec 23 to jan 02", "2026-12-23", "2027-01-02"),    # as before
        ("May 6 to 7", "2027-05-06", "2027-05-07"),
        # An end with its own month is a whole date, read by the named dates (CodeRabbit #279).
        ("May 6 to 7 June", "2027-05-06", "2027-06-07"),
        ("March 15-22 April", "2027-03-15", "2027-04-22"),
    ])
    def test_a_range_with_a_day_for_its_end(self, text, dep, ret):
        e = extract_entities(text)
        assert (e.departure_date, e.return_date) == (dep, ret)

    @pytest.mark.parametrize("text", [
        "march 5 - 10 days", "april 7 to 2 people", "june 9 to 12:30", "may 6 to 7 pm", "december 28 to 2 weeks",
    ])
    def test_a_length_a_count_or_a_time_is_no_return(self, text):
        e = extract_entities(text)
        assert e.departure_date is not None
        assert e.return_date is None

    def test_a_day_the_month_lacks_is_no_return(self):
        e = extract_entities("feb 27 to 30")
        assert (e.departure_date, e.return_date) == ("2027-02-27", None)

    @pytest.mark.parametrize("text,dep,ret", [
        ("we land on nov. 8 and fly home on nov. 16.", "2027-11-08", "2027-11-16"),
        ("Nov.14", "2027-11-14", None),
        ("6 nov. 2027", "2027-11-06", None),
    ])
    def test_a_month_with_a_period(self, text, dep, ret):
        e = extract_entities(text)
        assert (e.departure_date, e.return_date) == (dep, ret)

    @pytest.mark.parametrize("text", [
        "in Nov 2026", "sometime in June 2027", "dec 2026 - jan 2027", "January 2027 for two weeks",
    ])
    def test_a_month_with_its_year_is_no_day(self, text):
        e = extract_entities(text)
        assert e.departure_date is None and e.return_date is None


@freeze_time("2026-11-23")
class TestACountWithADashIsNoDate:
    """23 Nov 2026: a stay or a count written with a dash ("for 2-3 weeks", "10-14 days") was read as a month
    and a day (the 3rd of February, the 14th of October) and became the lead's departure. 7 such messages in
    60 days, 6 of them in chats that had an agent; #279 reads the agent's part by default. Phrasings invented."""

    @pytest.mark.parametrize("text", [
        "for 2-3 weeks", "10-14 days", "we'd stay 5-7 nights", "3-4 days in Rome", "a 3-4 hour layover",
        "1-2 stops is fine", "we are 3-4 people", "2-3 bags each", "give me 2-3 options", "10-15% off", "1-2 pm",
    ])
    def test_a_length_or_a_count_is_no_date(self, text):
        e = extract_entities(text)
        assert (e.departure_date, e.return_date) == (None, None)

    def test_the_count_is_still_read_as_travellers(self):
        assert extract_entities("we are 3-4 people").passengers == 4

    @pytest.mark.parametrize("text,dep,ret", [
        ("on 12-15", "2026-12-15", None),
        ("12-20 2 adults", "2026-12-20", None),
        ("12/20 for 2 weeks", "2026-12-20", None),
        ("dec 20 for 2-3 weeks", "2026-12-20", None),
        ("3/15 pm flight", "2027-03-15", None),             # a slash is a date, whatever follows
        ("12/20-1/5", "2026-12-20", "2027-01-05"),
        ("12/23/26-1/3/27", "2026-12-23", "2027-01-03"),
        ("12-15-2026 2 weeks", "2026-12-15", None),         # with its year, a date
    ])
    def test_a_date_written_in_digits_is_still_a_date(self, text, dep, ret):
        e = extract_entities(text)
        assert (e.departure_date, e.return_date) == (dep, ret)

    def test_a_range_ending_in_stops_is_no_return(self):
        e = extract_entities("march 5 to 2 stops")
        assert (e.departure_date, e.return_date) == ("2027-03-05", None)
