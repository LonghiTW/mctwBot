"""Quick compatibility check for zoneinfo + astral + APScheduler."""
from zoneinfo import ZoneInfo
from astral import LocationInfo
from astral.sun import sun
from datetime import date, datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

tz = ZoneInfo("Asia/Taipei")
city = LocationInfo("Taipei", "Taiwan", "Asia/Taipei", 25.0330, 121.5654)
s = sun(city.observer, date=date.today(), tzinfo=tz)
now = datetime.now(tz)

sched = AsyncIOScheduler(timezone=str(tz))
dt = DateTrigger(run_date=s["sunset"], timezone=tz)

print(f"Sunset today: {s['sunset']}")
print(f"Now: {now}")
print(f"DateTrigger run_date: {dt.run_date}")
print("All checks passed!")
