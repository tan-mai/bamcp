# BAMCP — BTC Analysis MCP

MCP server tự pull dữ liệu kline đa khung từ Binance và phục vụ phân tích Wyckoff/VSA/GANN từ bất kỳ thiết bị nào có Claude. Không cần cron job riêng.

Về mặt vận hành nó là một web API server bình thường: Starlette + uvicorn, Basic auth, healthcheck, chạy trong Docker sau reverse proxy. Điểm khác duy nhất là nó nói thêm được giao thức MCP ở đường `/mcp`.

Tuỳ chọn đọc tài khoản thật từ Binance, Bybit hoặc OKX bằng API key read-only — để nhật ký và báo cáo dựa trên số liệu sàn thay vì lời khai.

**Mọi cấu hình nằm trong `config.yaml`, secret nằm trong `.env`.** Không cần sửa `server.py` khi đổi máy.

## Tools

| Tool | Việc |
|---|---|
| `list_timeframes` | Khung nào có file, lần pull gần nhất, lỗi nếu có |
| `refresh_data` | Ép pull ngay từ Binance, không chờ chu kỳ |
| `get_klines` | Nến OHLCV thô, mặc định chỉ trả nến đã đóng |
| `get_context` | Range, vị trí giá trong range, spread/volume cho VSA, swing gần nhất, độ tươi dữ liệu |
| `get_bias` | Đọc bias đã lưu |
| `save_bias` | Lưu bias sau bước W/D/H4 |
| `get_rules` | Đọc quy định đang hiệu lực + lịch sử thay đổi |
| `update_rules` | Đổi quy định, bắt buộc kèm lý do, hiệu lực ngay |
| `get_today_status` | Quota lệnh + PnL, rule đổi hôm nay, check trước khi vào lệnh |
| `log_trade` | Ghi lệnh, cảnh báo nếu phạm rule |
| `close_trade` | Đóng lệnh, cập nhật PnL thực |
| `get_positions` | Vị thế đang mở **thật trên sàn** |
| `get_fills` | Lệnh đã khớp trong ngày, lấy thẳng từ sàn |
| `get_account_pnl` | PnL thật, tách lãi/lỗ, phí giao dịch, funding |
| `reconcile_journal` | Đối chiếu nhật ký tự ghi với sàn, chỉ ra chỗ lệch |

Không có tool đặt lệnh. Cố ý.

## Nến đang chạy vs nến đã đóng

`get_context` trả về ba khối tách bạch:

- `last_closed_bar` + `vsa` — tính trên **nến đã đóng** cuối cùng. Đây là thứ dùng để đọc VSA.
- `current_bar` — nến đang hình thành, kèm `completion_pct`. Chỉ dùng để biết giá hiện tại. Volume và spread của nó **vô nghĩa** với VSA vì nến chưa chạy xong.
- `freshness` — tuổi của nến đóng gần nhất. `stale: true` nghĩa là trễ hơn 2 nến, pipeline có vấn đề, đừng tin kết quả phân tích.

`get_klines` theo cùng kỷ luật đó: mặc định chỉ trả nến đã đóng, mỗi cây gắn cờ `is_closed`. Muốn thấy nến đang chạy phải gọi `include_forming=true`, và nó về với `is_closed: false`.

## Quy định giao dịch

Chia làm hai loại, sửa bằng hai đường khác nhau.

**Rule bằng số** — sống trong `data/rules.json`, đọc mới **mỗi lần gọi tool**, không cache. Đổi là có hiệu lực ngay, không restart.

| Rule | Mặc định |
|---|---|
| `max_trades_per_day` | 3 |
| `max_margin_per_trade` | 20 |
| `daily_stop_loss` | -20 |
| `max_stop_points` | 300 |
| `min_take_profit_points` | 500 |

Đổi ngay trong chat, từ bất kỳ thiết bị nào:

> *"Từ hôm nay giảm xuống 2 lệnh một ngày, tôi đang vào lệnh quá tay."*

Claude gọi `update_rules({"max_trades_per_day": 2}, reason="...")`. `reason` là tham số bắt buộc — không có lý do thì tool báo lỗi.

Khối `rules:` trong `config.yaml` chỉ là **giá trị khởi tạo**. Lần đầu `update_rules` chạy, bản sao được ghi sang `data/rules.json` và từ đó file đó là nguồn sự thật. Sửa `config.yaml` về sau chỉ có tác dụng khi bạn thêm một rule hoàn toàn mới.

Server chỉ chặn giá trị làm vỡ logic tính toán — `daily_stop_loss` phải `<= 0`, các rule còn lại phải `>= 0`, và tên rule phải có thật (gõ sai tên là báo lỗi chứ không âm thầm tạo rule mới). Ngoài ra nới hay siết bao nhiêu là quyền bạn.

**Mọi thay đổi đều để lại dấu.** `rules.json` giữ `history` đầy đủ: đổi gì, từ bao nhiêu sang bao nhiêu, lúc nào, vì sao. Và:

- `get_today_status` trả `rules_changed_today` — rule bị đổi trong chính ngày đang giao dịch thì hiện ra ngay ở bước check trước khi vào lệnh.
- `log_trade` ghi `rules_at_entry` vào từng lệnh — đọc lại nhật ký cũ vẫn biết lúc đó mình chơi theo luật nào.

Đổi rule giữa lúc đang lỗ thì vẫn đổi được. Nhưng bản ghi sẽ nói ra điều đó, và Claude được dặn phải nhắc lại trước khi bàn tiếp chuyện vào lệnh.

**Quy trình phân tích** — nằm ở key `instructions` trong `config.yaml`, không còn hardcode trong `server.py`. Claude đọc nó một lần lúc bắt tay, nên sửa xong cần `docker compose restart` rồi tắt/bật lại connector trong chat. Không phải build lại image.

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

Trang admin có luôn mục sửa 5 rule, và **đi qua đúng một cửa với Claude**: cùng hàm `_apply_rule_changes`, cùng validate, cùng ghi vào `rules.json`.

Nghĩa là **bắt buộc có lý do** dù bạn đổi ở đâu. Trang admin không phải cửa sau vòng qua cơ chế ghi dấu — nếu nó là cửa sau thì toàn bộ `rules_changed_today` và `rules_at_entry` mất giá trị.

Trang còn hiện 5 lần đổi gần nhất ngay dưới ô nhập, kèm giá trị cũ → mới và lý do. Việc siết/nới rule luôn nằm trước mắt chứ không chôn trong file JSON.

Chỉ những ô **thực sự đổi** mới được gửi lên, nên bấm Lưu mà không động vào rule thì không sinh bản ghi lịch sử rỗng.

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

Ba đường:

| Endpoint | Auth | Việc |
|---|---|---|
| `POST /mcp` | có | giao thức MCP — Claude nói chuyện ở đây |
| `GET /healthz` | không | health check cho Docker HEALTHCHECK và reverse proxy |
| `GET /admin` | có* | trang đặt username/password, credential sàn, quy định |

`*` `/admin` mở công khai khi **chưa** đặt mật khẩu lần nào, nhưng lúc đó phải có setup token mới ghi được. Xem mục [Trang admin](#trang-admin).

Không có REST API nào khác. Muốn pull dữ liệu ngay thì bảo Claude gọi tool `refresh_data`.

## Cấu trúc thư mục dữ liệu

```
<data_root>/          # Docker: /data, mount ra ./data trên host
├── klines/    1w.json  1d.json  4h.json  1h.json  15m.json
├── bias/      2026-09-12.json
├── rules.json            # quy định đang hiệu lực + lịch sử đổi
├── settings.json         # username/password (hash) + credential sàn — chmod 600
└── journal/   2026-09-12.json
```

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
