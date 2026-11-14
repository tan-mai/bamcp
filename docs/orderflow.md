# Order flow kiểu Bookmap

[← README](../README.md)

Dashboard vẽ order flow chồng lên chart nến, và Claude đọc cùng dữ liệu đó dưới dạng số qua tool MCP:

| Thành phần | Ý nghĩa | Nguồn |
|---|---|---|
| Heatmap thanh khoản | Lệnh limit đang chờ theo thời gian × giá, càng nóng càng lớn | Sổ lệnh (chỉ có từ lúc collector chạy) |
| Bong bóng | Lệnh market lớn đã khớp: xanh mua chủ động, đỏ bán chủ động, diện tích tỉ lệ khối lượng | aggTrade |
| CVD | Delta tích luỹ (mua chủ động − bán chủ động), pane phụ dưới chart | aggTrade |
| Volume Profile | Khối lượng khớp theo giá trong vùng đang xem, kèm POC / VAH / VAL | aggTrade |
| COB | Sổ lệnh hiện tại (bid / ask) ở cột phải | Sổ lệnh |

## Cách hoạt động

```
Binance ── depth@100ms (wss .../public/stream) ──┐
        ── aggTrade    (wss .../market/stream) ──┤
        ── REST snapshot / aggTrades ────────────┤
                                                 ▼
                         bamcp-orderflow (process riêng)
                                                 │ ghi
                                                 ▼
                                   orderflow.db (SQLite riêng, WAL)
                                                 │ đọc
                        ┌────────────────────────┼─────────────────────┐
                        ▼                        ▼                     ▼
              /setup-dashboard          tool MCP order flow      /admin/orderflow
```

- **Collector là process riêng**, không chạy trong server MCP. Collector dừng hay lỗi thì `/mcp` và dashboard vẫn chạy, chỉ là dữ liệu order flow cũ đi. Tool trả `data_coverage`, trang admin và menu Lớp báo rõ.
- Một process lo mọi cặp. Mỗi market có hai kết nối combined stream. Binance tách đường dẫn WebSocket USDⓈ-M futures từ 2026-04-23: depth ở `/public`, aggTrade ở `/market`, đường cũ `/ws`, `/stream` trên host gốc không còn dùng được. Spot vẫn ở `wss://stream.binance.com:9443/stream`.
- **Sổ lệnh cục bộ** đồng bộ đúng thuật toán Binance: buffer event, lấy snapshot REST, bỏ event cũ, event đầu phải phủ `lastUpdateId`, futures kiểm `pu`, spot kiểm `U = u trước + 1`. Sai thì resync và ghi gap. Mức giá xa giá hiện tại chỉ phản ánh lệnh quan sát được từ lúc theo dõi, vì snapshot REST chỉ có quanh giá. Chỉ lưu trong `depth_range_pct`.
- **Lệnh khớp** khử trùng theo `agg_trade_id` (lưu id cuối cho từng cặp). Nối lại sau mất mạng hay restart thì lấy bù phần thiếu qua REST `fromId`, nên mỗi lệnh chỉ được cộng một lần. Thiếu quá 200 trang REST thì ghi gap `trades`.
- Tự nối lại với backoff 1s → 60s, chủ động nối lại sau 23 giờ (Binance ngắt ở mốc 24 giờ). Mỗi lần nối lại hay resync ghi vào `orderflow_gaps`. Heartbeat ghi `orderflow_status` mỗi 10 giây.
- Snapshot heatmap mỗi `snapshot_interval_sec`: gom bucket quanh mid, hai mảng float32 nén zlib. Collector gộp sẵn trung bình 10s / 60s / 300s (`ob_snapshots_agg`) để heatmap khoảng dài không phải giải nén hàng chục nghìn snapshot. Trung bình có trọng số nên kết quả vẫn là trung bình các snapshot của từng cột.

## Cài và chạy

**Docker** (cách deploy hiện tại). `docker-compose.yml` và `docker-compose.prod.yml` có thêm service `bamcp-orderflow`: cùng image, cùng volume `bamcp_data`, chạy `python orderflow_collector.py`. Healthcheck dùng `python orderflow_collector.py --health`, kiểm heartbeat trong DB. Healthcheck mặc định của image gọi cổng 8848 nên không dùng được cho collector. Service này không mở cổng, không cần nối vào mạng Traefik, chỉ cần ra internet tới Binance. Trên panel không dùng compose thì tạo thêm một container cùng image và cùng volume, đặt lệnh `python orderflow_collector.py`.

**systemd** (cài thẳng trên VPS):

```bash
sudo cp bamcp-orderflow.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bamcp-orderflow
journalctl -u bamcp-orderflow -f
```

Unit có `Restart=always`. Log ra stderr (`docker compose logs bamcp-orderflow` / `journalctl`) và ghi thêm file `<data_root>/orderflow/logs/collector.log`, rotate 10 MB × 3 file.

**Chạy thử trên máy:**

```powershell
.venv\Scripts\python orderflow_collector.py --run-seconds 600   # chạy 10 phút rồi tự dừng
.venv\Scripts\python orderflow_collector.py --health            # exit 0 khi heartbeat còn mới
```

Cần thêm hai thư viện trong `requirements.txt`: `websockets` cho collector và `numpy` cho heatmap, tường và hấp thụ.

## Cấu hình

Khối `orderflow:` trong `config.yaml` là giá trị khởi tạo. Sửa ở trang **/admin/orderflow** thì bản mới ghi vào `paths.orderflow_config_file` (mặc định `orderflow/config.json` trên volume) và thắng khối yaml. Collector kiểm tra `mtime` của cả hai file mỗi 30 giây và áp dụng không cần restart: bật / tắt cặp thì subscribe / unsubscribe, đổi tham số thì có hiệu lực từ snapshot kế tiếp. File sai thì log lỗi và giữ cấu hình cũ. Trang admin cũng từ chối lưu giá trị sai.

| Trường | Ý nghĩa | BTC | ETH | XAU |
|---|---|---|---|---|
| `market` | `futures` (USDⓈ-M) hoặc `spot` | futures | futures | futures |
| `bucket_size` | Bước giá gom heatmap và footprint | 10 | 1 | 0.5 |
| `depth_range_pct` | Chỉ lưu sổ lệnh trong mid ± khoảng này | 0.03 | 0.03 | 0.02 |
| `snapshot_interval_sec` | Chu kỳ snapshot heatmap | 2 | 2 | 2 |
| `footprint_res_sec` | Độ phân giải footprint, phải chia hết 60 | 5 | 5 | 5 |
| `big_trade_min_qty` | Ngưỡng bong bóng, đơn vị base asset | 5 | 100 | 10 |
| `bubble_merge_ms` | Gộp aggTrade cùng phía liên tiếp trong khoảng này thành một lệnh, giá là VWAP | 200 | 200 | 200 |
| `wall_k` | Tường = qty ≥ k × median qty các bucket cùng phía trong range | 5 | 5 | 5 |
| `absorption_min_qty` | Ngưỡng khối lượng chủ động cho sự kiện hấp thụ | 50 | 1000 | 100 |

Số mặc định của XAU đo từ dữ liệu thật: tick 0.01, giá khoảng 3,900, khoảng 683 nghìn aggTrade mỗi ngày (2026-11-12), p99 một lệnh khoảng 4.8 oz. Ngưỡng bong bóng và hấp thụ là điểm khởi đầu, chỉnh theo thị trường.

Thời hạn lưu (`retention`, job chạy trong collector lúc 00:10 UTC hằng ngày, xong thì `PRAGMA incremental_vacuum`):

| Dữ liệu | Giữ |
|---|---|
| `ob_snapshots` và bản gộp sẵn | `heatmap_days` = 14 |
| `trade_footprint` res nhỏ (5s) | `footprint_5s_days` = 30, trước khi xoá gộp sang res 60s |
| `trade_footprint` res 60s | `footprint_60s_days` = 365 |
| `big_trades` | `big_trades_days` = 180 |
| `orderflow_gaps` | `gaps_days` = 365 |

Tham số kỹ thuật chỉ sửa trong yaml: `db_path`, `tmp_dir`, `log_dir`, `endpoints.<market>` (URL Binance), `heatmap_agg_levels`, `trade_gap_max_pages`, `reconnect_hours`.

## Dung lượng và tài nguyên

Đo thật trên máy dev, collector chạy BTCUSDT, ETHUSDT, XAUUSDT futures với cấu hình mặc định ở trên (2026-11-14):

| Đo | Kết quả |
|---|---|
| DB sau 4 giờ đầu, 3 cặp | 34 MB (khoảng 8.5 MB mỗi giờ, chưa có bản gộp sẵn) |
| BTC mỗi ngày | heatmap 72 MB + gộp sẵn 18 MB + footprint 6 MB + bong bóng 0.3 MB ≈ **96 MB** |
| ETH mỗi ngày | 26 + 6 + 4 + 0.5 ≈ 37 MB |
| XAU mỗi ngày | 54 + 13 + 3 + 0.1 ≈ 70 MB |
| Ổn định với thời hạn mặc định | khoảng 1.3 GB BTC, 0.5 GB ETH, 1 GB XAU, cộng footprint 60s và bong bóng của năm |
| RAM collector, 3 cặp | 111 MB working set (khoảng 37 MB mỗi cặp) |
| CPU collector, 3 cặp | 2.6% một core |

Heatmap BTC lớn hơn ước tính ban đầu (40–60 MB/ngày) vì với ±3% và bucket $10 mỗi snapshot có khoảng 405 bucket, nén còn khoảng 1.6 KB. Muốn nhẹ hơn: giảm `depth_range_pct` (0.02 bớt khoảng 1/3), tăng `bucket_size`, tăng `snapshot_interval_sec`, hoặc giảm `heatmap_days`.

## Backfill lệnh khớp

Heatmap **không backfill được**, vì Binance không có lịch sử sổ lệnh miễn phí. Lệnh khớp thì backfill được:

- Trang admin, mục **Backfill aggTrades**: chọn cặp, từ ngày, đến ngày (ngày UTC, tối đa 90 ngày mỗi lần). Tiến độ hiện ngay bên dưới.
- Claude: `backfill_orderflow(symbol, from_date, to_date)`, xem tiến độ bằng `get_orderflow_status`.
- Tay: `.venv\Scripts\python orderflow_backfill.py --symbol BTCUSDT --from 2026-11-07 --to 2026-11-13`

Ngày cũ tải file daily trên data.binance.vision về `tmp_dir`, xử lý xong thì xoá. Hôm nay, và ngày file chưa đăng (thường là hôm qua), dùng REST `aggTrades`: tìm mốc bằng cửa sổ ≤ 1 giờ rồi lật trang `fromId`, đọc `X-MBX-USED-WEIGHT-1M` để không vượt rate limit. Backfill ghi đè theo khoảng nên chạy lại bao nhiêu lần số dòng vẫn vậy. Phần hôm nay dừng trước mép ghi của collector đang chạy 60 giây, nên hai bên không bao giờ cùng ghi một bucket. Collector đang tắt thì backfill đẩy `last_agg_id` tới lệnh cuối, lần chạy sau lấy bù từ đó. Footprint ghi res 5s nếu ngày còn trong `footprint_5s_days`, cũ hơn thì ghi res 60s. Job chạy process riêng nên không chiếm CPU của server MCP.

Đo thật: 7 ngày BTCUSDT từ file mất 54 giây. Phần hôm nay qua REST, khoảng 8 giờ đầu ngày, mất khoảng 6 phút với 738 request.

## Trên dashboard

Menu **Lớp** có nhóm **Order Flow** ở cả trang chính lẫn full page:

```
☑ Order Flow  (cha, 3 trạng thái: tất cả / một phần / không)
   ☐ Heatmap thanh khoản         + slider độ tương phản
       ☐ Đường best bid/ask
       ☑ Tô vùng không có dữ liệu
   ☑ Bong bóng                   + ô min_qty
       ☑ Mua (BUY)   ☑ Bán (SELL)
   ☐ CVD                         + neo: Vùng đang xem / Ngày UTC
   ☑ Volume Profile
       ☑ Đường POC / VAH / VAL
   ☐ COB (sổ lệnh hiện tại)
```

- Mặc định: trang chính bật Bong bóng và Volume Profile. Full page bật tất cả trừ đường best bid/ask.
- Tắt một lớp thì không gọi API của lớp đó, ngừng nhận qua SSE / polling và giải phóng dữ liệu. Bật lại thì tải ngay cho vùng đang xem. Pan / zoom tải lại sau 100ms, chỉ khi khoá yêu cầu đổi.
- Tắt CVD thì pane CVD ẩn và chart chính cao lên, giữ nguyên zoom. Tắt cả Volume Profile lẫn COB thì cột 120px bên phải ẩn.
- Cặp chưa bật order flow, hoặc collector chưa có dữ liệu, thì cả nhóm mờ, kèm tooltip. Đã có lệnh khớp mà chưa có sổ lệnh thì chỉ Heatmap và COB mờ ("Chưa có dữ liệu sổ lệnh").
- Trạng thái nhớ riêng từng trang (`bamcp.orderflow.layers.main` / `.full`), gồm checkbox, độ tương phản, `min_qty` theo cặp và neo CVD. Full page nhận `of=heatmap,bbo,gaps,bubbles,buy,sell,cvd,vp,vplines,cob`. Nút "⤢ Mở full page" mang theo `of=`. Đổi checkbox ở full page thì URL cập nhật. Thứ tự ưu tiên: `of=` > localStorage > mặc định.
- Phím tắt ở full page: `H` heatmap, `B` bong bóng, `C` CVD, `V` volume profile, `O` COB.
- Live: SSE `/api/orderflow/stream` đẩy phần mới của các lớp đang bật mỗi `snapshot_interval_sec`. SSE lỗi thì chuyển sang polling 5 giây, 2 phút sau thử lại SSE. Review (ngày cũ) không mở stream.
- Heatmap nằm dưới nến, toạ độ lấy từ API của Lightweight Charts. Màu chuẩn hoá log theo percentile 99 của vùng đang xem, tính từ mức trung vị. Lưới chart ẩn khi heatmap bật. Vùng gap và vùng trước khi collector chạy tô xám sọc, kèm tooltip "Không có dữ liệu sổ lệnh".

Tài khoản view-only dùng được mọi checkbox và API order flow, vì không có gì nhạy cảm (không có vị thế, PnL). Trang `/admin/orderflow` vẫn chỉ vào được bằng phiên admin.

## Tool MCP

| Tool | Việc |
|---|---|
| `get_orderflow_summary` | N phút gần nhất: buy / sell vol, delta, CVD đầu và cuối kỳ (neo ngày UTC), bong bóng mỗi phía, imbalance ±0.5% và ±1%, top 3 tường mỗi phía |
| `get_liquidity_walls` | Tường thanh khoản: `ACTIVE` / `PULLED` (dấu hiệu spoof) / `CONSUMED` / `FADED` |
| `get_cvd` | Chuỗi CVD kèm nhận xét phân kỳ đơn giản |
| `get_volume_profile` | POC, VAH, VAL (value area 70%), top 3 HVN và LVN |
| `get_absorption_events` | Sự kiện hấp thụ `BULLISH_ABSORPTION` / `BEARISH_ABSORPTION` và `outcome_15m`. Cần có sổ lệnh |
| `get_orderbook_snapshot` | Sổ lệnh đã gom bucket tại thời điểm gần `ts` nhất |
| `backfill_orderflow` | Chạy backfill nền, trả job id |
| `get_orderflow_status` | Trạng thái collector từng cặp, dung lượng DB, job backfill gần đây |

Mọi tool trả `symbol`, `from`, `to` (ISO UTC) và `data_coverage` (gap, phần đầu / cuối chưa có dữ liệu). Giá làm tròn theo bucket. `from_time` / `to_time` nhận `YYYY-MM-DD HH:MM` giờ VN như các tool khác, ISO hoặc epoch ms. Quy trình phân tích, scheduled task bias và entry-alert M5 **chưa** dùng các tool này. Việc tích hợp để ticket sau.

## Kiểm tra trên dữ liệu thật

`tests/check_orderflow_live.py` đối chiếu dữ liệu đã thu với Binance. Script gọi mạng nên pytest không tự chạy:

```powershell
.venv\Scripts\python tests\check_orderflow_live.py --symbol BTCUSDT --day 2026-11-12 --collector-from "2026-11-14 04" --collector-hours 3
```

Script kiểm bốn việc: tổng một ngày so với file aggTrades, tổng theo giờ so với volume và taker buy của kline H1, POC / VAH / VAL so với cách tính độc lập từ lệnh thô, và các giờ collector tự ghi.

Lưu ý khi đọc kết quả: kline có cả giao dịch quỹ bảo hiểm / ADL, aggTrade thì không. Một aggTrade có thể gồm các lệnh vắt qua mốc giờ nhưng chỉ mang một thời điểm. Vì vậy tổng ngày khớp tuyệt đối với file, còn từng giờ có thể lệch vài chục BTC ở giờ có một lệnh lớn đúng mốc. Ví dụ 11:00 / 12:00 ngày 2026-11-12 lệch ±55.8 BTC (±0.6%), hai giờ bù trừ nhau. Kiểm tính liên tục của collector chính xác nhất bằng cách so với REST aggTrades cùng giờ.

## Bảng dữ liệu (`orderflow.db`)

Khoá chính GUID, `ts` là epoch ms UTC.

| Bảng | Nội dung |
|---|---|
| `ob_snapshots` | Snapshot heatmap: `mid`, `best_bid`, `best_ask`, `bucket_size`, `base_price`, `n_buckets`, `bids` / `asks` (float32 nén zlib) |
| `ob_snapshots_agg` | Trung bình gộp sẵn theo `res_sec` 10 / 60 / 300, kèm `n_snapshots` |
| `trade_footprint` | `buy_vol`, `sell_vol`, `buy_cnt`, `sell_cnt` theo `(symbol, res_sec, ts, price_bucket)` |
| `big_trades` | Bong bóng: `price` (VWAP), `qty`, `side`, `n_trades`, `first_agg_id`, `last_agg_id` |
| `orderflow_gaps` | `kind` = `depth` (mất sổ lệnh), `trades` (thiếu lệnh khớp), `conn` (nối lại aggTrade, đã lấy bù đủ) |
| `orderflow_status` | Heartbeat từng cặp: kết nối, event cuối, snapshot cuối, `last_agg_id`, số mức giá, resync hôm nay |
| `orderflow_jobs` | Job backfill và tiến độ |
