from fitbit_health.mcp_server import create_server


class FakeHealthService:
    @staticmethod
    def _metric(days: int, data: list | dict | None = None) -> dict:
        return {
            "requested_days": days,
            "available_days": 1,
            "data": data if data is not None else [],
            "missing_data": [],
            "diagnostics": {},
        }

    def get_sleep(self, days: int = 7) -> dict:
        return self._metric(days)

    def get_steps(self, days: int = 7) -> dict:
        return self._metric(days, [{"date": "2026-07-18", "steps": 2000}])

    def get_heart_rate(self, days: int = 7) -> dict:
        return self._metric(days)

    def get_resting_heart_rate(self, days: int = 7) -> dict:
        return self._metric(days)

    def get_hrv(self, days: int = 7) -> dict:
        return self._metric(days)

    def get_health_summary(self, days: int = 7) -> dict:
        return self._metric(days, {})


if __name__ == "__main__":
    create_server(service_factory=FakeHealthService).run(transport="stdio")
