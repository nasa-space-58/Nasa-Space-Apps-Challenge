"""Analyze local caches, optionally refining transitions through RabbitMQ only."""


async def run_analysis(data_dir, terrain_reference, horizons_reference, settings,
                       selected_utc=None, sensitivity_deg=0.75, refine=False, broker_url=None):
    """Compute a horizon, analyze samples and optionally ingest finer transition windows."""
    raise NotImplementedError


def main():
    """Print reproducible visibility JSON without writing the shared data volume."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
