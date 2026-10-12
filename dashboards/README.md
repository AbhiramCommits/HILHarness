# Dashboards

The **HIL Overview** dashboard visualises the Prometheus gauges the harness
exports (`results/hil_metrics.prom`):

| Panel | Metric(s) |
|---|---|
| Boot time (p50 / p95) | `hil_boot_ms_p50`, `hil_boot_ms_p95` |
| Driver init latency by driver | `hil_driver_init_ms_p95{driver=...}` |
| CAN frame loss | `hil_can_frame_loss_pct` |
| Target CPU / RSS under load | `hil_cpu_pct_max`, `hil_rss_mb_max` |
| Suite pass rate | `hil_suite_pass_rate` |
| Clean boot rate | `hil_clean_boot_rate` |
| Fault detection rate | `hil_fault_detection_pct` |

Two alert rules are defined inline on the boot-time and CAN-loss panels:
`boot_ms_p95 > 15000` and `can_frame_loss_pct > 1`.

## Run it

```bash
docker compose up -d          # prometheus :9090, pushgateway :9091, grafana :3000
hil prom --pushgateway http://localhost:9091   # push the latest gauges
# open http://localhost:3000  (anonymous enabled; admin/admin)
```

Grafana is provisioned from `dashboards/provisioning`; the dashboard JSON is
auto-loaded from `dashboards/hil_overview.json`.

## Screenshot / verify steps

A checked-in screenshot is intentionally omitted (the numbers would go stale).
To verify the dashboard renders:

1. `docker compose up -d`
2. `hil prom --pushgateway http://localhost:9091`
3. In Grafana, navigate to **HIL → HIL Overview** and confirm all seven panels
   resolve against the `Prometheus` datasource.
