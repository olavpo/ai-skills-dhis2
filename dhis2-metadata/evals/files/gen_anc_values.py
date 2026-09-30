"""Dummy ANC data generator (worked on our 2.41 test box).

Posts random monthly values for a few ANC data elements to every facility
under Bo district. Synthetic test data only.
"""
import random

import requests

BASE = "http://localhost:8080"
AUTH = ("admin", "district")
BO = "O6uvpzGd5pu"
DATA_ELEMENTS = ["fbfJHSPpUQD", "cYeuwXTCPkU", "Jtf34kNZhzP"]  # ANC 1st/2nd/3rd visit
PERIODS = [f"2026{m:02d}" for m in range(1, 13)]


def facilities():
    r = requests.get(f"{BASE}/api/organisationUnits", auth=AUTH, params={
        "filter": [f"path:like:{BO}", "level:eq:4"],
        "fields": "id", "paging": "false"})
    r.raise_for_status()
    return [ou["id"] for ou in r.json()["organisationUnits"]]


def main():
    values = []
    for ou in facilities():
        for pe in PERIODS:
            for de in DATA_ELEMENTS:
                values.append({"dataElement": de, "period": pe, "orgUnit": ou,
                               "value": str(random.randint(0, 80))})
    r = requests.post(f"{BASE}/api/dataValueSets", auth=AUTH,
                      json={"dataValues": values})
    print(r.status_code, r.text[:2000])


if __name__ == "__main__":
    main()
