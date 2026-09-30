# BAMCP — BTC Analysis MCP

MCP server tự pull dữ liệu kline đa khung, nhiều cặp giao dịch, từ Binance — phục vụ phân tích Wyckoff/VSA/GANN từ bất kỳ thiết bị nào có Claude. Không cần cron job riêng.

Về mặt vận hành nó là một web API server bình thường: Starlette + uvicorn, Basic auth, healthcheck, chạy trong Docker sau reverse proxy. Điểm khác duy nhất là nó nói thêm được giao thức MCP ở đường `/mcp`.

Tuỳ chọn đọc tài khoản thật từ Binance, Bybit hoặc OKX bằng API key read-only — để nhật ký và báo cáo dựa trên số liệu sàn thay vì lời khai.

**Mọi cấu hình nằm trong `config.yaml`, secret nằm trong `.env`.** Không cần sửa `server.py` khi đổi máy.

## Tools

| Tool | Việc |
|---|---|
| `list_timeframes` | Các cặp đang theo dõi, khung nào đã có dữ liệu, lần pull gần nhất |
| `refresh_data` | Ép pull ngay từ Binance, không chờ chu kỳ |
| `get_klines` | Nến OHLCV thô, mặc định chỉ trả nến đã đóng |
| `get_context` | Range, vị trí giá trong range, spread/volume cho VSA, swing gần nhất, độ tươi dữ liệu — theo cặp |
| `get_bias` | Đọc bias đã lưu của một cặp |
| `save_bias` | Lưu bias cho một cặp sau bước W/D/H4 |
| `get_rules` | Đọc quy định đang hiệu lực (kèm `symbol` để xem bộ số của một cặp) + lịch sử thay đổi |
| `update_rules` | Đổi quy định, bắt buộc kèm lý do; rule theo cặp thì bắt buộc kèm `symbol`; `orb=true` đổi bộ rule ORB riêng |
| `get_today_status` | Quota lệnh + PnL, rule đổi hôm nay, check trước khi vào lệnh |
| `check_trade` | Chấm thử một lệnh theo rule mà không ghi nhật ký |
| `log_trade` | Ghi lệnh, cảnh báo nếu phạm rule |
| `close_trade` | Đóng lệnh, cập nhật PnL thực |
| `get_positions` | Vị thế đang mở **thật trên sàn**, mọi cặp đang theo dõi |
| `get_fills` | Lệnh đã khớp trong ngày theo từng vị thế, mọi cặp |
| `get_account_pnl` | PnL thật, tách lãi/lỗ, phí, funding — và tách theo cặp |
| `reconcile_journal` | Đối chiếu nhật ký tự ghi với sàn, chỉ ra chỗ lệch |
| `list_orb_sessions` | Các phiên ORB, giờ mở kế tiếp (UTC + giờ VN), watch M5 có đang chạy |
| `get_opening_range` | High/Low của OR (nến M15 đầu phiên), ATR H1, cờ lọc `too_narrow`/`too_wide` |
| `check_orb_signal` | State của phiên, nến M5 kích hoạt, plan entry/SL/TP/qty đã tính phí |
| `skip_orb_session` | Bỏ một phiên trong ngày (bắt buộc lý do), dừng watch M5 |
| `backtest_orb` | Backtest ORB trên M5 lịch sử, tách in-sample/out-of-sample |
| `get_backtest_result` | Lấy kết quả backtest chạy lâu theo `run_id` |
| `get_orb_config` | Tham số + rule ORB đang áp dụng (kèm nguồn từng lớp), cảnh báo `rule_infeasible` |

Không có tool đặt lệnh. Cố ý. Plan của ORB chỉ là con số đề xuất — người dùng tự đặt lệnh trên sàn.

Bốn tool cũ nhận thêm tham số **tuỳ chọn** cho ORB; không truyền thì hành vi và các khoá trả về giữ nguyên như trước:

- `get_today_status(strategy, session_id)` và `get_account_pnl(strategy, session_id)` — thêm khối lọc theo chiến lược/phiên. Quota và `daily_stop_loss` chung chỉ đếm lệnh **không phải ORB**; ORB có quota và daily stop riêng (`orb_trades_remaining`, `orb_daily_stop_hit`, `can_trade_orb` — luôn có trong `get_today_status`).
- `check_trade(strategy="ORB", session_id, or_date)` — chấm bằng [bộ rule ORB riêng](#bộ-rule-orb-riêng) (`rule_set: "orb"`, lỗi rule nằm ở `orb_rule_violations`), kèm khối `orb_checks`: còn trong cửa sổ không, phiên còn quota không, range có bị lọc không.
- `log_trade(strategy="ORB", session_id, variant, or_date, or_high, or_low)` — ghi lệnh ORB và dừng watch M5 của phiên đó. Lệnh không phải ORB được gắn `strategy: "OTHER"`.

## Nến đang chạy vs nến đã đóng

`get_context` trả về ba khối tách bạch:

- `last_closed_bar` + `vsa` — tính trên **nến đã đóng** cuối cùng. Đây là thứ dùng để đọc VSA.
- `current_bar` — nến đang hình thành, kèm `completion_pct`. Chỉ dùng để biết giá hiện tại. Volume và spread của nó **vô nghĩa** với VSA vì nến chưa chạy xong.
- `freshness` — tuổi của nến đóng gần nhất. `stale: true` nghĩa là trễ hơn 2 nến, pipeline có vấn đề, đừng tin kết quả phân tích.

`get_klines` theo cùng kỷ luật đó: mặc định chỉ trả nến đã đóng, mỗi cây gắn cờ `is_closed`. Muốn thấy nến đang chạy phải gọi `include_forming=true`, và nó về với `is_closed: false`.

## Nhiều cặp giao dịch

Hệ thống theo dõi nhiều cặp, không chỉ BTC. Thêm và quản lý trong trang admin, mục **Cặp giao dịch**.

Khi thêm một cặp, server hỏi Binance xem cặp đó có thật không rồi mới nhận — không có danh sách cứng, nên cặp nào Binance có là dùng được. Thêm xong nó kéo dữ liệu ban đầu ngay, không phải chờ hết chu kỳ 15 phút.

Từ đó cặp mới được pull tự động ở cả 5 khung như BTC, cùng một vòng lặp nền.

### Cấu trúc dữ liệu

```
data/
├── klines/
│   ├── BTCUSDT/   1w.json  1d.json  4h.json  1h.json  15m.json
│   ├── ETHUSDT/   1w.json  ...
│   └── SOLUSDT/   ...
├── bias/
│   ├── BTCUSDT/   2026-09-15.json
│   └── ETHUSDT/   2026-09-15.json
├── journal/       2026-09-15.json     ← dùng chung mọi cặp
├── rules.json                          ← rule chung + phần đặt riêng của từng cặp
└── settings.json
```

Dữ liệu từ bản một-cặp được **tự động chuyển** sang bố cục này lúc khởi động, không mất gì.

### Cái gì riêng, cái gì chung

| | Phạm vi |
|---|---|
| Nến, bias | **Riêng từng cặp** |
| Quota lệnh/ngày, dừng ngày, hạn mức margin | **Chung tất cả các cặp** |
| SL tối đa, TP tối thiểu, ngưỡng swing | **Riêng từng cặp** |
| Nhật ký lệnh | Chung |
| Đọc tài khoản sàn | **Mọi cặp đang theo dõi** — vị thế, lệnh khớp, PnL |

Ngân sách rủi ro dùng chung là cố ý: nếu mỗi cặp một bộ quota thì thêm 5 cặp là nhân ngân sách rủi ro lên 5 lần, mà tài khoản thì vẫn chỉ có một.

Ngưỡng đo bằng **điểm giá** thì ngược lại, phải riêng: 300 điểm trên BTC là một nhịp nhỏ, 300 điểm trên ADA là cả một chu kỳ. Ép chung một con số thì hoặc quá chặt cho cặp này, hoặc quá lỏng cho cặp kia.

### Dùng trong chat

Mọi tool phân tích đều nhận `symbol`, bỏ trống là lấy cặp mặc định:

> *"Phân tích ETHUSDT theo Wyckoff từ khung tuần xuống H4"*

> *"So sánh cấu trúc BTC và ETH hiện tại"*

> *"Lưu bias short cho ETHUSDT, pha B"*

Phương pháp không đổi theo cặp — vẫn Wyckoff/VSA/GANN, vẫn từ khung lớn xuống nhỏ, vẫn chỉ đọc VSA trên nến đã đóng.

## Quy định giao dịch

Chia làm hai loại, sửa bằng hai đường khác nhau.

**Rule bằng số** — sống trong `data/rules.json`, đọc mới **mỗi lần gọi tool**, không cache. Đổi là có hiệu lực ngay, không restart.

Rule chia làm hai phạm vi.

**Chung cho cả tài khoản** — một bộ duy nhất, hết quota là hết dù lệnh nằm ở cặp nào:

| Rule | Mặc định |
|---|---|
| `max_trades_per_day` | 3 |
| `max_margin_per_trade` | 20 |
| `daily_stop_loss` | -20 |
| `swing_max_margin_per_trade` | 0 *(0 = không giới hạn)* |

**Riêng từng cặp** — mỗi cặp giữ ngưỡng của nó; con số dưới đây là mặc định, cặp nào chưa đặt riêng thì kế thừa đúng những số này:

| Rule | Mặc định |
|---|---|
| `max_stop_points` | 300 |
| `min_take_profit_points` | 500 |
| `swing_min_take_profit_points` | 2000 |
| `swing_max_stop_points` | 1500 |

Lệnh `strategy="ORB"` **không** chấm bằng các rule trên (trừ `max_margin_per_trade`) và không ăn quota/daily stop chung — ORB có bộ rule riêng, xem [Bộ rule ORB riêng](#bộ-rule-orb-riêng).

Đổi ngay trong chat, từ bất kỳ thiết bị nào:

> *"Từ hôm nay giảm xuống 2 lệnh một ngày, tôi đang vào lệnh quá tay."*

> *"ADA điểm nhỏ quá, SL tối đa của riêng ADAUSDT để 15 điểm thôi."*

Claude gọi `update_rules({"max_trades_per_day": 2}, reason="...")` cho rule chung, và `update_rules({"max_stop_points": 15}, reason="...", symbol="ADAUSDT")` cho rule riêng. `reason` là tham số bắt buộc ở cả hai — không có lý do thì tool báo lỗi.

**Không nhầm phạm vi được.** Gửi một rule chung kèm `symbol` thì tool từ chối chứ không âm thầm ghi nó vào một cặp — nếu không, bạn tưởng mình siết quota cả tài khoản trong khi thật ra chỉ siết mỗi ADA. Chiều ngược lại, `get_rules(symbol="ETHUSDT")` trả về bộ số đã giải cho ETH, kèm khối `symbol_overrides` cho biết cặp nào đang đặt riêng những rule gì.

Khối `rules:` trong `config.yaml` chỉ là **giá trị khởi tạo**. Lần đầu `update_rules` chạy, bản sao được ghi sang `data/rules.json` và từ đó file đó là nguồn sự thật. Sửa `config.yaml` về sau chỉ có tác dụng khi bạn thêm một rule hoàn toàn mới.

`rules.json` từ bản một-phạm-vi vẫn đọc được nguyên vẹn: nó chỉ có khối `values`, và mọi cặp kế thừa đúng những con số đang có — không cần bước chuyển đổi nào.

Server chỉ chặn giá trị làm vỡ logic tính toán — `daily_stop_loss` phải `<= 0`, các rule còn lại phải `>= 0`, tên rule phải có thật (gõ sai tên là báo lỗi chứ không âm thầm tạo rule mới), và cặp phải đang được theo dõi (gõ sai tên cặp thì không tạo ra một bộ rule mồ côi). Ngoài ra nới hay siết bao nhiêu là quyền bạn.

**Mọi thay đổi đều để lại dấu.** `rules.json` giữ `history` đầy đủ: đổi gì, từ bao nhiêu sang bao nhiêu, lúc nào, vì sao, và **cho cặp nào** — "siết cả tài khoản" với "siết mỗi ADA" là hai dòng khác nhau trong lịch sử. Và:

- `get_today_status` trả `rules_changed_today` — rule bị đổi trong chính ngày đang giao dịch thì hiện ra ngay ở bước check trước khi vào lệnh.
- `log_trade` ghi `rules_at_entry` vào từng lệnh — đọc lại nhật ký cũ vẫn biết lúc đó mình chơi theo luật nào.

Đổi rule giữa lúc đang lỗ thì vẫn đổi được. Nhưng bản ghi sẽ nói ra điều đó, và Claude được dặn phải nhắc lại trước khi bàn tiếp chuyện vào lệnh.

**Quy trình phân tích** — nằm ở key `instructions` trong `config.yaml`, không còn hardcode trong `server.py`. Claude đọc nó một lần lúc bắt tay, nên sửa xong cần `docker compose restart` rồi tắt/bật lại connector trong chat. Không phải build lại image.

## Phân tích ORB

ORB (Opening Range Breakout) chỉ chạy trên một cặp — `orb.symbol`, mặc định `BTCUSDT`. **OR = nến M15 đầu tiên** của mỗi phiên.

### Hai giai đoạn

1. **Chốt OR.** Nến M15 đầu phiên đóng + 20 giây (`candle_close_delay_seconds`), scheduler kéo M15 và H1, tính High/Low của OR và ATR(14) của H1 tính đến trước giờ mở. `OR / ATR` dưới `min_or_atr_ratio` là `too_narrow`, trên `max_or_atr_ratio` là `too_wide` — phiên bị lọc thì **không kéo M5**.
2. **Watch M5.** OR hợp lệ thì cứ 5 phút (nến M5 đóng + 20 giây) kéo M5 một lần, tìm breakout theo `entry.mode`. Watch dừng khi phiên `TAKEN` (đã `log_trade`), `SKIPPED` (đã `skip_orb_session`), hoặc hết `trade_window_minutes`. Ngoài cửa sổ đó không có request M5 nào.

Mọi giờ tính theo timezone IANA của từng phiên nên tự đổi giờ mùa hè — London 08:00 là 07:00Z vào mùa hè, 08:00Z vào mùa đông. Tool trả cả UTC lẫn giờ VN.

State của một phiên trong một ngày:

```
WAITING_OPEN → FORMING → RANGE_SET → BREAKOUT_LONG / BREAKOUT_SHORT
                                    → FAILED_BREAKOUT_LONG / FAILED_BREAKOUT_SHORT
kết thúc: FILTERED · EXPIRED · SKIPPED · TAKEN
```

Bộ lọc khác: `use_bias_filter` (so với bias đã `save_bias` trong ngày), `skip_news_days` (ngày có tin lớn), quota `max_trades` mỗi phiên và `orb.max_trades_per_day` (đếm theo `or_date`).

Plan tính sẵn phí taker + trượt giá hai chiều vào 1R. `qty` = `orb.margin_usd × orb.leverage / entry`, làm tròn **xuống** theo `qty_step` — 20 × 100 / 83,500 → 0.023 BTC. Dưới `min_notional_usd` thì plan có cảnh báo, BAMCP không tự tăng size. Mỗi plan còn được chấm bằng rule ORB (`plan.rule_check`, `filters.rules`); trượt rule hoặc đã chạm daily stop ORB thì `blocked_by` chứa `rules` — plan vẫn trả về để xem vì sao, nhưng không phải lệnh để vào.

### Bộ rule ORB riêng

Lệnh ORB có SL/TP nhỏ hơn hẳn scalp/swing, nên được chấm bằng một bộ rule riêng: khối `orb` trong `data/rules.json`. `check_trade`, `log_trade`, `check_orb_signal`, `get_today_status` và `backtest_orb` đều dùng chung một hàm giải rule nên không lệch nhau.

| Rule | BTC | Việc |
|---|---|---|
| `max_stop_points` | 350 | SL tối đa |
| `min_take_profit_points` | 200 | TP tối thiểu |
| `min_rr` | 1.5 | R:R tối thiểu (TP / SL) |
| `max_trades_per_day` | 2 | quota lệnh ORB mỗi `or_date` — riêng, không ăn quota chung |
| `daily_stop_loss` | -25 | PnL ORB trong `or_date` chạm ngưỡng thì chặn ORB — lệnh khác vẫn vào được |
| `margin_usd` × `leverage` | 20 × 100 | sizing của plan |
| `maint_margin_pct`, `liq_safety_pct` | 0.4, 80 | SL phải nằm trong 80% khoảng cách tới giá thanh lý (≈354 điểm ở 83,500) |

Tham số chiến lược cũng nằm ở đây: `min_or_atr_ratio` (0.3), `max_or_atr_ratio` (0.7), `tp_r` (1.5), `buffer_pct` (0.02), `move_sl_to_be_at_r`, `time_exit_minutes`, `use_bias_filter`, `allow_reversal`, `skip_news_days`.

Ba lớp, lớp trên đè lớp dưới: **phiên** (chỉ tham số chiến lược) → **cặp** (`symbol_overrides`) → **`orb.defaults`**. `get_orb_config` ghi rõ mỗi giá trị lấy từ lớp nào (`source`). Cặp chưa đặt SL/TP ORB thì lệnh ORB bị chặn với lỗi `chua dat rule ORB cho <CẶP>.<field>` — server không tự đoán ngưỡng. `orb.enabled = false` là công tắc nghiệp vụ: không phiên nào chốt OR/watch M5, `check_orb_signal` trả `orb_disabled`, `check_trade(strategy="ORB")` báo ORB đang tắt.

Sửa bằng một trong hai đường, cả hai bắt buộc lý do, mỗi trường đổi là một dòng `history` (nguồn `mcp` hoặc `admin`), có hiệu lực ngay:

- chat: `update_rules({"min_rr": 2}, reason="...", symbol="BTCUSDT", orb=true)` — bỏ trống `symbol` là sửa `orb.defaults`, `session_id="ny"` là override của phiên, giá trị `null` là bỏ override;
- mục **Rule ORB** trên `/admin/orb`: chọn phạm vi, sửa, xem trước bộ rule đã giải kèm cảnh báo trước khi lưu. Mục này hiện luôn số lệnh ORB đã dùng và PnL ORB hôm nay.

**Cảnh báo `rule_infeasible`.** Theo ATR H1 và giá hiện tại, server kiểm xem bộ số có tự mâu thuẫn không — vd `min_take_profit_points` 900 với `tp_r` 1.5 và `max_or_atr_ratio` 1.2 ở ATR 462 thì TP tối đa chỉ ~832, mọi tín hiệu đều trượt; hoặc OR tối đa + buffer đã vượt `max_stop_points`. Cảnh báo hiện trong `get_orb_config.warnings`, trên trang admin và ngay ở bước xem trước. Nó không chặn lưu.

Lần đầu chạy bản này, khối `orb` được tạo **một lần** từ giá trị cũ trong `config.yaml` (nếu còn) + mặc định BTC ở trên, kèm một dòng `history` nguồn `migrate`. Các field cũ trong `config.yaml` (`risk.*`, `exit.tp_r`, `filters.max_or_atr_ratio`, `max_orb_trades_per_day`...) từ đó bị bỏ qua và được liệt kê trong `get_orb_config.warnings` dạng `deprecated: ...` — xoá chúng khỏi `config.yaml` là hết cảnh báo.

### Cấu hình

Tham số **kỹ thuật** nằm ở khối `orb:` trong `config.yaml` (sửa xong phải restart): `entry.mode`, `failed_lookback_bars`, `exit.sl_mode`, khung và chu kỳ ATR, costs, `exchange_limits`, đường dẫn dữ liệu. Rule và tham số chiến lược nằm trong `rules.json` — xem [Bộ rule ORB riêng](#bộ-rule-orb-riêng). Bật/tắt nhanh cả module (scheduler, tool, trang admin) bằng `BAMCP_ORB_ENABLED=true|false`.

Danh sách **phiên** thì quản lý ở trang `/admin/orb`, lưu trong `data/orb/sessions.json`, **không cần restart**. `default_sessions` trong config chỉ là hạt giống cho lần chạy đầu. Trên trang đó:

- thêm/sửa phiên: `session_id`, tên, timezone, giờ mở, ngày giao dịch, cửa sổ, quota, override kỹ thuật (vd `entry_mode`). Override **rule** của phiên (OR/ATR, `tp_r`, `buffer_pct`...) sửa ở mục Rule ORB — form phiên giữ nguyên chúng và từ chối nếu bị sửa ở đây;
- **Xem trước** giờ mở kế tiếp theo UTC và giờ VN trước khi lưu;
- bật/tắt, xoá phiên. Tắt hay xoá giữa phiên thì watch đang chạy dừng ngay;
- lịch sử thay đổi.

`session_id` không đổi được sau khi tạo — state, nhật ký và backtest đều gắn vào nó.

Ngày có tin lớn: thêm/xoá ở mục **News days** trên `/admin/orb`. File là `data/orb/news_days.json` dạng `[{"date": "2026-10-02", "note": "CPI"}]` (dạng cũ `["2026-10-02"]` vẫn đọc được), đọc lại mỗi lần tính, không cần restart. `skip_news_days = true` thì phiên của ngày đó bị lọc (`news_day: true`, `filtered: true`), không ra plan.

### Dùng trong chat

> *"Phiên New York hôm nay OR thế nào, có tín hiệu chưa?"*

Claude gọi `get_opening_range` → `check_orb_signal`, đọc `trigger_candle` (nến M5 **đã đóng**) và `plan`. Plan có `blocked_by` chứa `rules` thì đọc `plan.rule_check.rule_violations` — không vào. Muốn vào lệnh thì `check_trade(strategy="ORB", session_id="ny")`, bạn tự đặt lệnh trên sàn, rồi `log_trade(strategy="ORB", session_id="ny", variant="breakout")`. Giá đã chạy xa, không đuổi: `skip_orb_session("ny", reason="...")`.

### Backtest

> *"Backtest ORB 2 năm, lấy 2026-03-01 làm mốc out-of-sample."*

`backtest_orb(from_date, to_date, session_ids, split_date, overrides, initial_equity, rules_override)` chạy trên M5 lịch sử. Lần đầu nó tải M5 từ `data.binance.vision` (file tháng, tháng đang chạy ghép từ file ngày, có kiểm SHA256) — khoảng nửa phút cho 2 năm; sau đó đọc cache dưới một giây. Chạy quá ~55 giây thì tool trả `status: running` kèm `run_id`, gọi `get_backtest_result(run_id)` sau.

Kết quả gồm: số lệnh, win rate, expectancy theo R, profit factor, drawdown, chuỗi thua dài nhất, tách in-sample/out-of-sample và theo phiên, lý do thoát (tp/sl/breakeven/time), số tín hiệu bị lọc hoặc không vào được vì quy mô tài khoản, và `data_gaps` cho những ngày thiếu dữ liệu. Nến M5 chạm cả SL lẫn TP thì tính là **thua** (`sl_tp_same_bar`). File `trades.csv`, `equity.csv`, `result.json` nằm trong `data/orb/backtests/<run_id>/`.

Mỗi lệnh giả lập được chấm bằng rule ORB hiện hành, kể cả quota và daily stop ORB theo ngày: kết quả có `rules_used` (bộ rule đã dùng), `rejected_by_rule` (số lệnh bị loại theo từng rule) và `stopped_days` (ngày chạm daily stop ORB). `rules_override` — vd `{"min_take_profit_points": 900}` — thử bộ số khác cho riêng lần chạy đó, không sửa `rules.json`.

## Đọc tài khoản thật từ sàn

Mặc định tắt (`account.enabled: false`) — khi đó mọi con số về lệnh và PnL đều là **lời khai của bạn** qua `log_trade` / `close_trade`. Bật lên thì server đọc thẳng từ sàn.

Hỗ trợ ba sàn, mỗi sàn một kiểu ký chữ ký riêng, cùng trả về một dạng dữ liệu chuẩn hoá:

| Sàn | `exchange` | Symbol mặc định | Cần passphrase |
|---|---|---|---|
| Binance USDT-M Futures | `binance` | `BTCUSDT` | không |
| Bybit V5 | `bybit` | `BTCUSDT` | không |
| OKX V5 | `okx` | `BTC-USDT-SWAP` | **có** |

### Tạo API key

Làm đúng ba việc này, theo thứ tự:

1. **Chỉ bật quyền đọc.** Tắt Enable Futures/Spot Trading, tắt Enable Withdrawals. Key kiểu này đặt lệnh không được, rút tiền không được.
2. **Bật IP whitelist**, chỉ điền IP của VPS. Key lọt ra ngoài cũng vô dụng ở chỗ khác.
3. **OKX còn đòi passphrase** — chuỗi bạn tự đặt lúc tạo key, không phải mật khẩu đăng nhập.

`exchanges.py` không có hàm nào đặt lệnh, huỷ lệnh, chuyển tiền hay rút tiền. Nhưng đừng dựa vào đó — dựa vào quyền của key.

### Cấu hình

Key và secret **không bao giờ** nằm trong `config.yaml`. Chúng đi qua `.env` như password:

```bash
BAMCP_EXCHANGE=binance
BAMCP_EXCHANGE_KEY=...
BAMCP_EXCHANGE_SECRET=...
BAMCP_EXCHANGE_PASSPHRASE=       # chỉ OKX
```

Rồi bật trong `config.yaml`:

```yaml
account:
  enabled: true
  exchange: "binance"
```

Server **từ chối khởi động** nếu bật `account.enabled` mà thiếu credential hoặc sai tên sàn — báo lỗi rõ ngay lúc boot thay vì hỏng âm thầm lúc gọi tool.

### Nó đổi gì trong `get_today_status`

Đây là thay đổi quan trọng nhất. Khi bật account, bước check trước khi vào lệnh không còn tin nhật ký nữa:

- `trades_taken` = `max(số lệnh trong nhật ký, số order trên sàn)` — vào lệnh mà quên log thì vẫn bị tính vào quota.
- `realized_pnl` = **net thật** từ sao kê sàn: lãi/lỗ đã chốt **cộng** phí giao dịch **cộng** funding. Con số này gần như luôn xấu hơn số bạn tự khai, vì log tay hầu như không bao giờ ghi phí và funding.
- `counted_from` cho biết đang đếm theo `exchange` hay `journal`.
- `journal_pnl` vẫn được giữ riêng để bạn thấy khoảng lệch.

Nếu mất kết nối tới sàn, `get_today_status` **không lỗi** — nó quay về dùng số trong nhật ký và đặt `exchange.error` để bạn biết con số đang kém tin cậy. Rào chắn kỷ luật không được phép sập vì mạng chập chờn.

### Một vị thế = một lệnh

TP từng phần và SL là những **order riêng** trên sàn, nhưng theo cách hiểu của người giao dịch chúng vẫn thuộc **một lệnh**. Đếm theo `order_id` sẽ thổi phồng số lệnh trong ngày và làm quota hết sớm giả.

Cả ba sàn đều không trả về position id trong lịch sử fill, nên server dựng lại: cộng dồn khối lượng có dấu, về 0 là đóng vị thế. Fill có `realized_pnl` khác 0 là fill đóng bớt, bằng 0 là fill mở hoặc thêm vào.

Ví dụ thật — bốn fill trong một ngày:

```
00:39  sell 7.27   pnl −11.03  ┐ P1  mở từ hôm trước
15:13  sell 2.30   pnl  −3.78  ┘     (carried_in)
15:43  buy  11.25  pnl      0  ┐ P2  mở và đóng trong ngày
16:24  sell 11.25  pnl −13.62  ┘
```

Trước đây tính là **4 lệnh**. Giờ là **2 vị thế**, và chỉ **1** tính vào quota hôm nay — vị thế mang từ hôm trước sang đã tính vào quota của hôm đó rồi, tính lại là phạt bạn hai lần.

`get_fills` trả về danh sách vị thế, mỗi vị thế có `position_id`, `carried_in`, `closed`, tổng `realized_pnl` và các fill thành phần. `get_today_status` và `reconcile_journal` đều đếm theo vị thế.

Các trường hợp đã kiểm: TP từng phần (mở 10, chốt 3+3+4 → 1 vị thế), scale in (5+5 rồi đóng 10 → 1), hai vị thế độc lập → 2, vị thế còn mở cuối ngày → 1 với `still_open`.

### Đọc tài khoản theo danh sách cặp

Bốn tool đọc tài khoản — `get_positions`, `get_fills`, `get_account_pnl`, `reconcile_journal` — đều chạy trên **toàn bộ danh sách cặp trong trang admin**. Thêm một cặp ở đó là nó tự động được đọc, không phải khai lại ở đâu.

Mỗi tool nhận `symbol` để lọc về một cặp; bỏ trống là gộp tất cả.

**Mã sàn tự suy.** Cặp phân tích là `ETHUSDT`, nhưng mỗi sàn gọi một kiểu:

| Cặp | Binance | Bybit | OKX |
|---|---|---|---|
| `BTCUSDT` | `BTCUSDT` | `BTCUSDT` | `BTC-USDT-SWAP` |
| `ETHUSDT` | `ETHUSDT` | `ETHUSDT` | `ETH-USDT-SWAP` |

Khai sẵn dạng có dấu gạch (`BTC-USDT-SWAP`) thì giữ nguyên — dùng cho mã không suy ra được theo quy tắc.

**Vị thế gom riêng từng cặp.** Khối lượng cộng dồn của BTC và ETH không thể trộn vào một chuỗi; trộn vào là ranh giới vị thế sai hoàn toàn. `position_id` mang tiền tố cặp: `BTCUSDT-P1`.

**Một cặp lỗi không kéo đổ cả lần gọi.** Cặp sàn không niêm yết được ghi vào `errors` rồi đi tiếp. Cặp lỗi còn được nhớ trong 30 phút để không làm chậm mọi lần gọi sau — ví dụ `XAUUSDT` có trên Binance nhưng có thể không có trên OKX.

`get_today_status` giờ có thêm `by_symbol` để thấy PnL từng cặp, và `errors` để biết cặp nào đọc không được — số liệu thiếu thì phải nhìn thấy, không được im lặng.

### `reconcile_journal`

Chạy cuối ngày, hoặc bất cứ lúc nào nghi mình quên log. Nó chỉ ra ba loại lệch:

- Sàn ghi nhận nhiều order hơn nhật ký → có lệnh chưa log
- Nhật ký nhiều hơn sàn → có lệnh log nhưng không khớp
- PnL lệch quá `pnl_tolerance` → thường là phí và funding chưa được tính

Nó **không tự sửa nhật ký**. Chỉ nói ra chỗ lệch, để bạn quyết định.

## Trang admin

`/admin` — đặt username/password cho API, credential sàn, và quy định giao dịch bằng giao diện, thay vì biến môi trường. Lưu xuống `<data_root>/settings.json`, nằm trong volume nên **sống sót qua mọi lần tạo lại container**.

Đây là điểm quan trọng khi deploy bằng panel không có SSH: bạn không phải nhập lại secret mỗi lần recreate container.

### Lần đầu sau khi deploy

Không có tài khoản mặc định nào trong mã nguồn. Mật khẩu đầu tiên vào bằng một trong hai đường:

**Cách khuyên dùng — biến môi trường lúc tạo container.** Điền hai ô này trong panel cùng lúc với các biến khác:

```
BAMCP_USERNAME=<tên bạn chọn>
BAMCP_PASSWORD=<chuỗi ngẫu nhiên, tối thiểu 8 ký tự>
```

Container khởi động là đăng nhập được ngay, không có cửa sổ nào để hở.

**Cách dự phòng — setup token.** Nếu không đặt hai biến trên, server in ra log lúc khởi động:

```
BAMCP CHUA DUOC CAI DAT - chua co mat khau nao.
  BAMCP SETUP TOKEN: y-rpoLBrJySnC4rluqovvZXtV1fJmQ8-9MHrzQkOWJk
```

Lúc này `/admin` mở công khai nhưng **không ghi được gì nếu thiếu token**, và `/mcp` vẫn trả 401. Token đổi mỗi lần khởi động lại và mất tác dụng ngay khi có mật khẩu.

### Sau khi đăng nhập

Trang admin có ba mục, làm hết trong một lần:

1. **Truy cập API** — đổi username/mật khẩu về sau
2. **Tài khoản sàn** — chọn binance/bybit/okx, dán API key read-only, bấm *Kiểm tra kết nối sàn* để xác nhận ngay tại chỗ
3. **Quy định giao dịch** — 5 rule + ô lý do, kèm lịch sử 5 lần đổi gần nhất

Bấm Lưu một lần là cả ba mục cùng ghi. Mọi thay đổi có hiệu lực ngay, không cần khởi động lại.

Xong bước này, `.env` và biến môi trường không còn vai trò gì nữa. Từ đó bạn quản lý theo hai đường, cùng đọc ghi một chỗ:

| | Trang admin | Claude |
|---|---|---|
| Mật khẩu API | có | không |
| Credential sàn | có | không |
| Quy định giao dịch | có | `get_rules` / `update_rules` |
| Xem lịch sử đổi rule | có | `get_rules` |

Mật khẩu và credential sàn cố ý **không** cho Claude sửa — chúng là thứ bảo vệ chính Claude, không nên nằm trong tầm với của nó.

### Biến môi trường: chỉ cần bốn dòng

Credential sàn **không cần** đi qua biến môi trường. Đặt chúng trong trang admin sau khi container chạy — an toàn hơn, vì key không nằm trong ô config của panel.

Bốn biến bắt buộc:

```
BAMCP_USERNAME=<tên bạn chọn>
BAMCP_PASSWORD=<chuỗi ngẫu nhiên, tối thiểu 8 ký tự>
BAMCP_ALLOWED_HOSTS=btc.domain.com,btc.domain.com:*
TZ=Asia/Ho_Chi_Minh
```

Bốn biến `BAMCP_EXCHANGE*` chỉ là đường nạp sẵn cho tiện. Bỏ trống thì container khởi động với phần đọc sàn **tắt** — log không có dòng `BAMCP account`, các tool `get_positions` / `get_fills` báo lỗi rõ ràng cho tới khi bạn cấu hình.

Sau đó vào `/admin`, mục **Tài khoản sàn**: chọn sàn, dán key/secret/passphrase, tích *Đọc dữ liệu tài khoản thật*, bấm **Kiểm tra kết nối sàn**, rồi Lưu. Có hiệu lực ngay, không cần khởi động lại — kể cả khi đổi hẳn sang sàn khác.

### Thứ tự ưu tiên

```
settings.json  >  biến môi trường  >  config.yaml
```

Biến môi trường chỉ còn là **giá trị khởi tạo** cho lần chạy đầu. Sau khi bấm Lưu, sửa `.env` không còn tác dụng — nếu không thì bấm Lưu xong mà không thấy gì đổi, kiểu lỗi khó hiểu nhất.

### Cách trang này giữ secret

- **Mật khẩu API lưu dạng hash** PBKDF2-SHA256, 200k vòng, salt riêng. Không có chỗ nào lưu plaintext.
- **Secret của sàn buộc phải lưu đọc lại được** — cần nó để ký request, mà server phải tự khởi động lại không có người gõ tay. File `chmod 600`. Không có cách nào tránh, và mã hoá tại chỗ chỉ là hình thức vì khoá giải mã cũng phải nằm cạnh.
- **Server không bao giờ gửi secret về trình duyệt.** Trang chỉ nhận "đã có / chưa có" và 4 ký tự cuối của API key — đủ để đối chiếu với trang sàn, không đủ để dùng lại.
- **Ô để trống = giữ nguyên.** Muốn xoá thì xoá ở `settings.json`.

Đổi trong trang admin có hiệu lực ngay, không cần khởi động lại — kể cả bật/tắt đọc tài khoản sàn.

### Quy định giao dịch

Trang admin sửa được cả hai phạm vi, và **đi qua đúng một cửa với Claude**: cùng hàm `_apply_rule_changes`, cùng validate, cùng ghi vào `rules.json`.

- Thẻ **Quy định chung** — 4 rule áp cho mọi cặp.
- Thẻ **Quy định theo từng cặp** — chọn cặp trong ô *Đang sửa cặp* rồi nhập; dòng chú thích ngay dưới cho biết cặp đó đang đặt riêng những rule gì, hay đang kế thừa toàn bộ mặc định. Sửa nhiều cặp rồi bấm Lưu một lần cũng được: những gì bạn nhập cho từng cặp được giữ lại khi đổi lựa chọn.
- Thẻ **Lý do & lịch sử** — một ô lý do dùng chung cho cả lần lưu, và 5 lần đổi gần nhất kèm phạm vi (`[chung]` hay `[ADAUSDT]`), giá trị cũ → mới, lý do.

Nghĩa là **bắt buộc có lý do** dù bạn đổi ở đâu, phạm vi nào. Trang admin không phải cửa sau vòng qua cơ chế ghi dấu — nếu nó là cửa sau thì toàn bộ `rules_changed_today` và `rules_at_entry` mất giá trị.

Chỉ những ô **thực sự đổi** mới được gửi lên, nên bấm Lưu mà không động vào rule thì không sinh bản ghi lịch sử rỗng.

Bỏ một cặp khỏi danh sách theo dõi **không** xoá phần rule đặt riêng của nó trong `rules.json` — giống như file nến cũ vẫn được giữ lại. Thêm cặp đó trở lại thì ngưỡng riêng cũ có hiệu lực lại luôn.

### Nút "Kiểm tra kết nối sàn"

Gọi thật lên sàn bằng credential đang lưu, chỉ đọc vị thế. Trả về ví dụ:

```
Ket noi okx thanh cong - 1 vi the dang mo (BTC-USDT-SWAP).
```

Sai key, sai passphrase hoặc IP chưa whitelist đều hiện ra ở đây thay vì phải mò trong log.

## Authentication

HTTP Basic. Username/password đọc theo thứ tự: biến môi trường `BAMCP_USERNAME` / `BAMCP_PASSWORD` trước, `server.auth` trong `config.yaml` sau.

Server **từ chối khởi động** nếu `auth.enabled: true` mà thiếu credential — không có đường nào vô tình chạy trần. Muốn tắt hẳn khi test local thì đặt `auth.enabled: false`.

So sánh credential dùng `secrets.compare_digest` cho cả hai vế, không short-circuit.

`/healthz` là đường public duy nhất; `/mcp` cần auth.

## Bề mặt HTTP

Các đường chính:

| Endpoint | Auth | Việc |
|---|---|---|
| `POST /mcp` | có | giao thức MCP — Claude nói chuyện ở đây |
| `GET /healthz` | không | health check cho Docker HEALTHCHECK và reverse proxy |
| `GET /admin` | có* | trang đặt username/password, credential sàn, quy định |
| `GET /admin/orb` | có* | quản lý phiên, rule ORB và news days (chỉ có khi `orb.enabled`) |
| `POST /admin/orb/sessions` | có* | xem trước / lưu / bật / tắt / xoá phiên — trang trên gọi |
| `POST /admin/orb/rules` | có* | xem trước / lưu rule ORB, thêm / xoá news day — trang trên gọi |

`*` `/admin` mở công khai khi **chưa** đặt mật khẩu lần nào, nhưng lúc đó phải có setup token mới ghi được. Xem mục [Trang admin](#trang-admin).

Không có REST API nào khác. Muốn pull dữ liệu ngay thì bảo Claude gọi tool `refresh_data`.

## Cấu trúc thư mục dữ liệu

```
<data_root>/          # Docker: /data, mount ra ./data trên host
├── klines/    1w.json  1d.json  4h.json  1h.json  15m.json
├── bias/      2026-09-12.json
├── rules.json            # rule chung + phần đặt riêng từng cặp + khối orb (rule ORB) + lịch sử đổi
├── settings.json         # username/password (hash) + credential sàn — chmod 600
├── journal/   2026-09-12.json
│   └── orb_skips/  2026-09-12.json      # phiên ORB đã skip, kèm lý do
└── orb/
    ├── sessions.json     # danh sách phiên + lịch sử đổi (trang /admin/orb)
    ├── news_days.json    # ngày có tin lớn — sửa ở /admin/orb
    ├── state/     ny/2026-09-12.json    # state + watch của một phiên trong một ngày
    ├── logs/      2026-09-12.jsonl      # log sự kiện: chốt OR, tick M5, dừng watch
    ├── history/   BTCUSDT/5m/*.csv      # M5 lịch sử cho backtest
    └── backtests/ <run_id>/  result.json  trades.csv  equity.csv
```

`klines/<cặp>/5m.json` chỉ xuất hiện khi ORB watch hoặc khi gọi `refresh_data(timeframes=["5m"])`; vòng pull 15 phút không bao giờ kéo khung 5m.

Mỗi chu kỳ (mặc định 15 phút) server gọi Binance, merge theo `openTime` nên không trùng nến, và giữ tối đa `max_history` nến.

Nếu bạn có pipeline khác ghi vào cùng thư mục thì đặt `fetcher.enabled: false` để tắt phần pull tích hợp.

**Quan trọng — `fetcher.market`:** để `futures` nếu bạn trade perpetual swap. Volume spot và volume perp khác nhau, mà VSA thì sống bằng volume.

## Deploy: GitHub Actions → ghcr.io → panel + Traefik

Dành cho VPS không có quyền root, chỉ tạo container được qua giao diện (Hostinger). Không cần Docker trên máy cá nhân, không cần SSH vào VPS.

```
domain.com → DNS → IP VPS → cổng 80/443 → Traefik → đọc Host header → container bamcp
```

### 1. Đẩy code lên GitHub

```bash
git init && git add -A && git status --short
```

Trước khi commit, `git status` **không được** hiện `.env`, `data/`, `.venv/`. Thấy `.env` là dừng lại — nó chứa key OKX thật.

```bash
git commit -m "BAMCP" && git branch -M main
git remote add origin https://github.com/<user>/bamcp.git && git push -u origin main
```

[.github/workflows/publish.yml](.github/workflows/publish.yml) tự chạy, build image và đẩy lên `ghcr.io/<user>/bamcp:latest`. Xem tiến trình ở tab **Actions**.

### 2. Mở quyền đọc cho image

Package trên ghcr mặc định private, mà VPS không có shell để `docker login`. Vào `github.com/users/<user>/packages` → chọn `bamcp` → **Package settings** → **Change visibility** → Public.

Repo vẫn để private được — GHCR tách riêng hai thứ. Image không chứa secret nào (`.env` bị `.dockerignore` loại, `config.yaml` có `password: ""`); thứ lộ ra chỉ là code và bộ rule giao dịch.

### 3. Tạo container trong panel

| Ô | Giá trị |
|---|---|
| Container name | `bamcp` |
| Image | `ghcr.io/<user>/bamcp:latest` |
| Restart policy | `unless-stopped` |
| Ports | **để trống** — Traefik gọi thẳng vào container |
| Volumes | `bamcp_data:/data` |
| Mạng | chọn mạng của Traefik |

**Volume phải là named volume, không phải host path.** Không có shell thì không `chown` được thư mục trên host, mà container chạy uid 10001. Named volume thì Docker chép quyền sở hữu từ image sang nên ghi được ngay — `Dockerfile` đã `chown -R bamcp:bamcp /data` trước dòng `USER bamcp` đúng vì lý do này.

### 4. Biến môi trường

Chỉ bốn dòng:

| Biến | Giá trị |
|---|---|
| `BAMCP_USERNAME` | tên đăng nhập bạn chọn |
| `BAMCP_PASSWORD` | chuỗi ngẫu nhiên, tối thiểu 8 ký tự |
| `BAMCP_ALLOWED_HOSTS` | `btc.domain.com,btc.domain.com:*` |
| `TZ` | `Asia/Ho_Chi_Minh` |

**Credential sàn không đặt ở đây.** Sau khi container chạy, vào `/admin` để dán key — key không nằm trong ô config của panel, và đổi sàn về sau không phải sửa container.

`BAMCP_ALLOWED_HOSTS` là chỗ dễ sập nhất: Traefik chuyển tiếp nguyên Host header, thiếu domain thật trong danh sách là server trả **421** cho mọi request.

### 5. Nhãn Traefik

```
traefik.enable=true
traefik.http.routers.bamcp.rule=Host(`btc.domain.com`)
traefik.http.routers.bamcp.entrypoints=websecure
traefik.http.routers.bamcp.tls.certresolver=<tên resolver trên VPS>
traefik.http.services.bamcp.loadbalancer.server.port=8848
```

Dòng cuối bắt buộc — container mở cổng 8848, Traefik không tự đoán được.

### 6. Kiểm tra

Xem log container, phải thấy:

```
BAMCP account: okx (read-only)
BAMCP data_root=/data symbol=BTCUSDT market=futures fetcher=True
INFO:     Uvicorn running on http://0.0.0.0:8848
```

Rồi từ máy bất kỳ:

```bash
curl.exe -s https://btc.domain.com/healthz
curl.exe -i -s -u "sai:sai" -X POST https://btc.domain.com/mcp
```

Cái thứ hai phải ra `401`. Ra `421` nghĩa là `BAMCP_ALLOWED_HOSTS` thiếu domain.

`probe.py` nằm sẵn trong image, nên nếu panel cho chạy lệnh trong container:

```bash
python probe.py get_positions
```

### 7. Cập nhật về sau

`git push` → Actions build lại image → vào panel bấm recreate container. Dữ liệu trong `bamcp_data` không mất.


`bamcp.service` là bản systemd cho trường hợp cài thẳng lên máy. Khi đó nhớ đổi `server.host` về `127.0.0.1` trong `config.yaml`.

## Chạy thử trên Windows

```powershell
cd C:\Working\AI\BAMCP
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
$env:BAMCP_USERNAME = "bamcp"
$env:BAMCP_PASSWORD = "test123"
.venv\Scripts\python server.py
```

Mở `http://127.0.0.1:8848/healthz`.

## Chạy test

```powershell
.venv\Scripts\pip install pytest
.venv\Scripts\python -m pytest tests -q
```

Test không gọi mạng và không đụng `data/` thật: chúng dùng thư mục tạm, sàn giả và đồng hồ giả. Bộ test gồm logic ORB (`test_orb_logic.py`), scheduler + watch M5 (`test_orb_runtime.py`), trang admin + backtest (`test_orb_admin_backtest.py`), bộ rule ORB riêng theo các acceptance criteria của BR (`test_orb_rules.py`), và tầng tool của server — trong đó có kiểm tra tool cũ không đổi khi không truyền tham số ORB (`test_server.py`).

## Gắn vào Claude

Customize → Connectors → Add custom connector:

- **URL:** `https://btc.yourdomain.com/mcp`
- **Authentication:** No sign-in
- **Request headers:** `Authorization` = `Basic <chuỗi base64>`

Sinh chuỗi base64:

```bash
printf '%s' "$BAMCP_USERNAME:$BAMCP_PASSWORD" | base64
```

Dán kết quả vào sau chữ `Basic` và một dấu cách. Ví dụ `Basic YmFtY3A6czNjcjN0`.

Connector gắn theo tài khoản nên có ngay trên mobile, desktop cá nhân và máy văn phòng miễn là đăng nhập cùng tài khoản. Bật trong từng chat qua nút "+".

Nếu hộp thoại không có mục **Request headers** thì tài khoản chưa được mở beta đó. Phương án tạm: đặt `auth.enabled: false`, đổi `mcp_path` thành một chuỗi ngẫu nhiên dài (`/mcp-a7f3...`), và coi URL đó như mật khẩu.

## Ba chỗ dễ sập

**Host header.** Traefik chuyển tiếp nguyên Host header của request. `BAMCP_ALLOWED_HOSTS` phải chứa domain thật, nếu không server trả 421 cho mọi request. Dạng `host:*` mới khớp mọi port; riêng `"*"` không phải wildcard.

**IP của VPS.** Binance chặn một số vùng (đáng chú ý: VPS đặt ở Mỹ) và trả `HTTP 451`. Nếu `list_timeframes` báo `last_fetch_error` ngay sau khi khởi động, kiểm tra bằng `curl -I https://fapi.binance.com/fapi/v1/ping` từ chính VPS đó trước khi nghi ngờ code.

**Volume.** Container chạy uid 10001. Dùng **named volume** (`bamcp_data:/data`), đừng bind mount vào thư mục host — không có shell thì không `chown` được, và `save_bias` sẽ lỗi permission ngay lần ghi đầu.
