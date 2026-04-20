from datetime import datetime, timezone


def utc_now_naive():
    """
    Return the current UTC time as a naive datetime.

    JS8Call timestamps in DIRECTED.TXT are recorded in UTC but parsed as naive
    datetimes. Comparing them against local naive datetimes causes freshness to
    drift by the local UTC offset and by DST changes. Using naive UTC on the
    comparison side keeps those age calculations stable without changing how the
    original JS8Call date/time text is displayed in the UI.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
