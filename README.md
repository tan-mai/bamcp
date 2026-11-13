# BAMCP

MCP server giúp Claude phân tích thị trường crypto bằng số liệu thật, và giúp bạn giữ kỷ luật khi giao dịch.

BAMCP tự kéo nến từ Binance cho nhiều cặp giao dịch. Claude đọc dữ liệu đó để phân tích theo Wyckoff, VSA và GANN, chấm lệnh theo quy định bạn đặt và ghi nhật ký. Server tự canh giá theo kế hoạch và báo qua Discord hoặc Telegram.

> **BAMCP không đặt lệnh.** Không có tool nào làm việc đó, và đây là chủ ý. Bạn luôn tự vào lệnh trên sàn.

## Cách hoạt động

```
Binance (nến) ───────────┐
Sàn (tuỳ chọn, chỉ đọc) ─┤
                         ▼
                       BAMCP ◀── Claude gọi tool qua /mcp
                         │
                         ├── /admin ............ bạn cấu hình
                         ├── /setup-dashboard .. xem biểu đồ, review
                         └── Discord/Telegram .. báo khi giá chạm kế hoạch
```

## Có gì trong BAMCP

| Tính năng | Làm gì | Chi tiết |
|---|---|---|
| Dữ liệu | Tự kéo nến 6 khung (5m đến 1w) cho nhiều cặp, lưu JSON hoặc SQLite | [data.md](docs/data.md) |
| Quy định | Chấm lệnh theo quota, SL/TP, R:R. Mỗi lần đổi quy định đều phải có lý do | [rules.md](docs/rules.md) |
| Setup và cảnh báo | Lưu kế hoạch vào lệnh, server canh trên nến M5 đã đóng rồi báo tin | [setups.md](docs/setups.md) |
| Dashboard | Vẽ bias, setup, kịch bản lên biểu đồ nến và review theo ngày | [dashboard.md](docs/dashboard.md) |
| Lệnh ngoài kế hoạch | Đối chiếu vị thế thật với setup trong ngày, báo lệnh lệch kế hoạch kèm mã OP để đánh giá cùng Claude | [off-plan.md](docs/off-plan.md) |
| ORB | Tín hiệu và backtest Opening Range Breakout theo phiên | [orb.md](docs/orb.md) |
| GANN | Pivot, cửa sổ thời gian, mốc giá Square of 9 | [gann.md](docs/gann.md) |
| Tài khoản thật (tuỳ chọn) | Đọc vị thế và PnL từ Binance, Bybit, OKX bằng key chỉ đọc | [account.md](docs/account.md) |

Tra cứu: [danh sách tool](docs/tools.md) · [trang admin và bảo mật](docs/admin.md) · [deploy](docs/deploy.md)

## Chạy thử trên máy

Cần Python 3.12. Lệnh dưới đây dùng PowerShell. Trên macOS/Linux, đổi `.venv\Scripts\` thành `.venv/bin/` và `copy` thành `cp`.

```powershell
git clone https://github.com/tan-mai/bamcp.git
cd bamcp
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Mở `.env`, điền `BAMCP_PASSWORD` (**tối thiểu 8 ký tự**), rồi chạy:

```powershell
.venv\Scripts\python server.py
```

Kiểm tra bằng một terminal khác:

| Kiểm tra | Kết quả đúng |
|---|---|
| Mở `http://127.0.0.1:8848/healthz` | `{"status":"ok",...}` |
| Mở `http://127.0.0.1:8848/admin` | Trang cài đặt, đăng nhập bằng username và mật khẩu trong `.env` |
| `.venv\Scripts\python probe.py` | Danh sách tool |
| `.venv\Scripts\python probe.py list_timeframes` | Các cặp đang theo dõi và khung đã có dữ liệu |

## Gắn vào Claude

Connector của Claude gọi server từ internet nên cần URL HTTPS công khai. Hãy [deploy](docs/deploy.md) trước, rồi:

1. Sinh chuỗi base64 từ username và mật khẩu:

   ```powershell
   [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("<username>:<mật khẩu>"))
   ```

2. Trong Claude, vào **Customize → Connectors → Add custom connector**:

   | Ô | Giá trị |
   |---|---|
   | URL | `https://<domain>/mcp` |
   | Authentication | No sign-in |
   | Request headers | `Authorization` = `Basic <chuỗi base64>` |

3. Bật connector trong chat qua nút **+**, rồi hỏi thử: *"Đang theo dõi những cặp nào?"*

Connector gắn theo tài khoản nên dùng được ngay trên web, desktop và mobile. Không thấy mục **Request headers**? Xem [phương án tạm](docs/deploy.md#không-thấy-mục-request-headers).

## Một ngày dùng BAMCP

| Lúc | Bạn nói với Claude | Chuyện xảy ra |
|---|---|---|
| Đầu ngày | *"Phân tích BTCUSDT từ khung tuần xuống H4, lưu bias và lập setup hôm nay."* | Claude đọc `get_context`, rồi gọi `save_bias`, `save_setups`, `save_scenarios` |
| Trong ngày | Không cần làm gì | Server canh setup và báo tin mỗi lần setup đổi trạng thái |
| Trước khi vào lệnh | *"Long BTC 67500, SL 67300, TP 68000, margin 20 USD được không?"* | `get_today_status` và `check_trade` chấm thử, không ghi gì. Bạn tự đặt lệnh trên sàn, rồi bảo Claude `log_trade` |
| Cuối ngày | *"Đối chiếu nhật ký với sàn và review setup hôm nay."* | `reconcile_journal` chỉ ra chỗ lệch, `save_review` lưu kết quả |

Xem thêm: [VSA chỉ đọc trên nến đã đóng](docs/data.md#nến-đang-chạy-và-nến-đã-đóng).

## Cấu hình nằm ở đâu

| Muốn đổi | Đổi ở đâu | Có hiệu lực |
|---|---|---|
| Mật khẩu, key sàn, cặp theo dõi | `/admin` | Ngay |
| Quy định giao dịch | `/admin` hoặc bảo Claude, bắt buộc có lý do | Ngay |
| Phiên ORB, GANN, lịch kéo nến, cảnh báo, tài khoản dashboard | Các trang con của `/admin` | Ngay |
| Tham số kỹ thuật, quy trình phân tích của Claude | `config.yaml` | Sau khi restart |

Thứ tự ưu tiên là `settings.json` > biến môi trường > `config.yaml`. Biến môi trường chỉ là giá trị khởi tạo cho lần chạy đầu, nên sau khi đã lưu trên `/admin` thì sửa `.env` không còn tác dụng.

## Lỗi thường gặp

| Triệu chứng | Nguyên nhân | Cách sửa |
|---|---|---|
| Đúng mật khẩu vẫn bị 401, log có `CANH BAO: bo qua BAMCP_PASSWORD` | Mật khẩu ngắn hơn 8 ký tự | Đổi mật khẩu dài hơn rồi khởi động lại |
| `/mcp` trả 421 | `BAMCP_ALLOWED_HOSTS` thiếu domain | [Thêm domain](docs/deploy.md#4-biến-môi-trường) |
| `list_timeframes` báo `last_fetch_error`, HTTP 451 | Binance chặn IP của VPS | [Kiểm tra IP](docs/deploy.md#ba-chỗ-dễ-sập) |
| `save_bias` lỗi permission | Volume là bind mount | [Dùng named volume](docs/deploy.md#ba-chỗ-dễ-sập) |
| Sửa `.env` không thấy đổi | `settings.json` được ưu tiên | Đổi trên `/admin` |

## Phát triển

```powershell
.venv\Scripts\pip install pytest
.venv\Scripts\python -m pytest tests -q
```

Test không gọi mạng và không đụng `data/` thật. Chúng dùng thư mục tạm, sàn giả và đồng hồ giả.

| File | Vai trò |
|---|---|
| `server.py` | Điểm vào: tool MCP, route HTTP, tác vụ nền |
| `settings.py`, `admin*.py` | `settings.json` và các trang admin |
| `kline_store.py`, `sqlite_db.py`, `fetch_schedules.py` | Lưu và kéo nến |
| `exchanges.py` | Đọc tài khoản sàn, không có hàm đặt lệnh |
| `orb*.py` | ORB |
| `gann_*.py` | GANN |
| `entry_*.py`, `notifier.py` | Setup, watcher, thông báo |
| `dashboard_*.py`, `static/` | Setup dashboard |
| `probe.py`, `bamcp_cli.py` | Gọi thử tool và lệnh quản trị từ dòng lệnh |
