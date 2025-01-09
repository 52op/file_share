from datetime import datetime, timedelta


def format_datetime(dt):
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def parse_date_range(date_from, date_to):
    try:
        if not date_from and not date_to:
            return None

        if date_from:
            from_date = datetime.strptime(date_from, "%Y-%m-%d")
        else:
            from_date = datetime.min

        if date_to:
            to_date = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        else:
            to_date = datetime.max

        return (from_date, to_date)
    except ValueError:
        return None
