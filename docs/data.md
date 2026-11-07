# Dữ liệu và nến

[← README](../README.md)

Cách BAMCP kéo, lưu và đọc nến. Đọc trang này khi thêm cặp, đổi lịch kéo nến hoặc chuyển sang SQLite.

## Nến đang chạy và nến đã đóng

`get_context` trả về ba khối tách bạch:

- `last_closed_bar` và `vsa`: tính trên **nến đã đóng** cuối cùng. Đây là thứ dùng để đọc VSA.
- `current_bar`: nến đang hình thành, kèm `completion_pct`. Chỉ dùng để biết giá hiện tại. Volume và spread của nó **vô nghĩa** với VSA vì nến chưa chạy xong.
- `freshness`: tuổi của nến đóng gần nhất. `stale: true` nghĩa là trễ hơn 2 nến, pipeline có vấn đề, đừng tin kết quả phân tích.

`get_klines` theo cùng kỷ luật đó: mặc định chỉ trả nến đã đóng, mỗi cây gắn cờ `is_closed`. Muốn thấy nến đang chạy phải gọi `include_forming=true`, và nó về với `is_closed: false`.

## Theo dõi nhiều cặp

Thêm và quản lý cặp ở `/admin`, mục **Cặp giao dịch**.

Khi thêm một cặp, server hỏi Binance xem cặp đó có thật không rồi mới nhận. Không có danh sách cứng, nên cặp nào Binance có là dùng được. Thêm xong, server kéo ngay nến ban đầu của các khung trong `klines.timeframes` (1w, 1d, 4h, 1h, 15m). Từ đó cặp được kéo tự động theo [lịch](#lịch-kéo-nến), gồm cả 5m.

Mọi tool phân tích đều nhận `symbol`, bỏ trống là lấy cặp mặc định:

> *"Phân tích ETHUSDT theo Wyckoff từ khung tuần xuống H4"*

> *"So sánh cấu trúc BTC và ETH hiện tại"*

> *"Lưu bias short cho ETHUSDT, pha B"*

Phương pháp không đổi theo cặp: vẫn Wyckoff/VSA/GANN, vẫn từ khung lớn xuống nhỏ, vẫn chỉ đọc VSA trên nến đã đóng.

### Cái gì riêng, cái gì chung

| | Phạm vi |
|---|---|
| Nến, bias | **Riêng từng cặp** |
| Quota lệnh/ngày, dừng ngày, hạn mức margin | **Chung tất cả các cặp** |
| SL tối đa, TP tối thiểu, ngưỡng swing, R:R | **Riêng từng cặp** |
| Nhật ký lệnh | Chung |
| Đọc tài khoản sàn | **Mọi cặp đang theo dõi**: vị thế, lệnh khớp, PnL |

Lý do chia như vậy nằm ở [rules.md](rules.md#hai-phạm-vi).

## Lịch kéo nến

`fetcher.mode: schedules` (mặc định): mỗi khung một schedule độc lập, cron 6 trường có giây, giờ **UTC**. Quản lý ở `/admin/schedules`: bật/tắt, thêm, sửa, xoá, "Chạy ngay", xem trước 3 lần chạy kế tiếp. Thay đổi có hiệu lực ngay, scheduler tự đọc lại bảng mỗi `reload_seconds`.

Bảng `fetch_schedules` trong `bamcp.db` rỗng thì lúc khởi động tạo 6 schedule mặc định (`seed_defaults`), mỗi lượt chạy 5 giây sau khi nến đóng:

| name | timeframe | cron (UTC) |
|---|---|---|
| pull-5m | 5m | `5 */5 * * * *` |
| pull-15m | 15m | `5 */15 * * * *` |
| pull-1h | 1h | `5 0 * * * *` |
| pull-4h | 4h | `5 0 */4 * * *` |
| pull-1d | 1d | `5 0 0 * * *` |
| pull-1w | 1w | `5 0 0 * * MON` |

Mỗi lượt, với từng cặp:

- Kéo từ nến mới nhất trong store lùi `overlap_candles` nến (tự vá lỗ gần), tới hiện tại. Merge theo `openTime` nên không trùng nến. Bản JSON giữ tối đa `max_history` nến.
- Nến vừa đóng chưa có trên Binance thì thử lại sau 3 giây, tối đa 3 lần.
- Khoá theo (cặp, khung): lượt trước chưa xong thì lượt sau ghi `skipped`. `refresh_data`, watch ORB và `backfill_klines` dùng chung khoá đó.
- Ghi `last_run_at`, `last_status` (`ok` / `error` / `skipped`), `last_error`, `last_duration_ms`. `list_timeframes` trả các trường đó cho từng khung, còn `last_fetch_error` là lỗi gần nhất của bất kỳ schedule nào.

Các tình huống khác:

- **Sau deploy:** backfill 90 ngày 5m cho mỗi cặp: `backfill_klines(timeframe="5m", symbol="...", days=90, max_requests=60)`. Nên chạy sau khi đã sang `kline_store: sqlite`, vì bản JSON cắt 5m theo `max_history`.
- **Khởi động lại** (`run_on_start: true`): schedule nào đã lỡ lịch trong lúc server tắt, tức lần chạy kế tiếp sau `last_run_at` đã qua, được chạy bù ngay một lần.
- **Rollback:** `fetcher.mode: interval` chạy lại vòng lặp `interval_seconds` cũ. Bảng `fetch_schedules` giữ nguyên để bật lại sau.
- **Task phân tích bias** lúc 7h VN nên đặt 00:05 UTC, sau khi đã có nến 4h và 1d.
- **Pipeline ngoài:** nếu có pipeline khác ghi vào cùng thư mục thì đặt `fetcher.enabled: false` để tắt phần pull tích hợp.

**Quan trọng với `fetcher.market`:** để `futures` nếu bạn trade perpetual swap. Volume spot và volume perp khác nhau, mà VSA thì sống bằng volume.

## Lưu nến trong SQLite

Nến đọc/ghi qua một interface (`kline_store.IKlineStore`) với hai bản: `json` (file như dưới) và `sqlite` (`<data_root>/bamcp.db`, bảng `klines`). Bias, journal, rules vẫn là JSON.

```yaml
storage:
  kline_store: "json"      # json | sqlite. Giá trị mặc định, trang /admin/schedules được ưu tiên
  sqlite_path: "bamcp.db"  # tương đối so với data_root
```

Bản `sqlite` chỉ lưu nến **đã đóng** (nến có `close_time > now` bị bỏ ngay ở tầng store), nên `current_bar` của `get_context` luôn rỗng. Không cắt theo `retention`. Bảng tự tạo khi khởi động.

### Đổi nơi lưu

Đổi ở mục **Nơi lưu nến** đầu trang `/admin/schedules`. Bảng ở đó cho thấy số nến và nến cuối của từng cặp × khung ở cả hai bên.

Bấm chuyển thì server giữ khoá fetch của các cặp, chép nến sang store đích (JSON → SQLite: chép hết; SQLite → JSON: chép theo `retention` của từng khung), đối chiếu, khớp mới đổi. Còn lệch thì không đổi và trang liệt kê cặp × khung bị lệch. Lựa chọn lưu vào `settings.json`, có hiệu lực ngay, không cần restart, giữ nguyên qua redeploy.

### Rollout an toàn

1. Deploy với `kline_store: json`, chạy import: `python kline_migrate.py import` (hoặc bảo Claude gọi `migrate_klines(action="import")`). Chạy lại nhiều lần không sinh trùng.
2. Kiểm tra song song: `python kline_migrate.py verify` hoặc `migrate_klines(action="verify")`. Lệnh so số nến, `open_time` đầu/cuối và OHLCV từng nến cho mỗi cặp × khung. Phải ra `mismatches: 0`. Nến JSON mới về sau lần import cũng tính là lệch, khi đó chạy lại import rồi verify.
3. Bấm **Chuyển sang SQLite** ở `/admin/schedules` (bước này tự import và verify lại lần cuối). Giữ thư mục `klines/` 4 tuần để rollback bằng nút **Chuyển về JSON**, sau đó mới xoá.

### Backup

Cần chạy ở nơi có lệnh `sqlite3` và thấy được file DB. Ví dụ cron mỗi đêm, giữ 14 bản. Không copy thẳng file khi DB đang mở:

```
0 3 * * * sqlite3 /data/bamcp.db ".backup /backup/bamcp-$(date +\%F).db" && ls -1t /backup/bamcp-*.db | tail -n +15 | xargs -r rm --
```

## Thư mục dữ liệu

```
<data_root>/              # Docker: /data. Chạy local: ./data
├── klines/<SYMBOL>/      # 1w.json 1d.json 4h.json 1h.json 15m.json 5m.json (khi kline_store = json)
├── bamcp.db              # SQLite: klines (khi kline_store = sqlite), fetch_schedules, setups,
│                         # setup_events, setup_params, scenarios, trading_ranges, plan_snapshots,
│                         # setup_reviews, dashboard_*
├── settings.json         # username/password (hash), credential sàn, danh sách cặp. chmod 600
├── rules.json            # rule chung, phần đặt riêng từng cặp, khối orb, lịch sử đổi
├── bias/<SYMBOL>/        # 2026-09-12.json
├── journal/              # 2026-09-12.json, dùng chung mọi cặp
│   └── orb_skips/        # 2026-09-12.json: phiên ORB đã skip, kèm lý do
├── alerts/config.json    # notify và watcher do /admin/alerts ghi (webhook, bot token)
├── dashboard/config.json # khối dashboard do /admin/dashboard-accounts ghi
├── time_windows/         # GANN: pivots/<SYMBOL>.json, manual_pivots.json, events.json, backtests/
└── orb/
    ├── sessions.json     # danh sách phiên và lịch sử đổi (trang /admin/orb)
    ├── news_days.json    # ngày có tin lớn, sửa ở /admin/orb
    ├── state/            # ny/2026-09-12.json: state và watch của một phiên trong một ngày
    ├── logs/             # 2026-09-12.jsonl: log sự kiện chốt OR, tick M5, dừng watch
    ├── history/          # BTCUSDT/5m/*.csv: M5 lịch sử cho backtest
    └── backtests/        # <run_id>/ result.json trades.csv equity.csv
```

Dữ liệu từ bản một-cặp được **tự động chuyển** sang bố cục này lúc khởi động, không mất gì.
