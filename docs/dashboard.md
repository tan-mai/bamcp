# Setup dashboard

[← README](../README.md)

Trang `https://<domain>/setup-dashboard` vẽ bias, setup, kịch bản và TR của từng cặp lên biểu đồ nến, cập nhật theo watcher, kèm chế độ review theo ngày. Trang chỉ đọc: không endpoint nào đặt, sửa, huỷ lệnh hay setup. Ngoại lệ duy nhất là lưu kết quả review.

Deep link, cũng là dạng `dashboard_url` mà tool và tin push trả về: `/setup-dashboard?symbol=ETHUSDT&date=2026-11-07&mode=review&tf=15m`.

## Bắt đầu

1. Tạo tài khoản ở `/admin/dashboard-accounts`. Dashboard có đăng nhập riêng, không dùng chung với admin.
2. Nếu deploy ở domain khác, đổi `dashboard.base_url` để link trong tool và tin báo trỏ đúng trang.

## Quy trình cho Claude

1. `save_bias` → `save_setups` → `save_scenarios(symbol, scenarios, tr=...)`. Mỗi lần lưu sinh một snapshot bất biến (`plan_snapshots`).
2. Cần vẽ biểu đồ inline thì gọi `get_chart_data`, cùng một nguồn với trang.
3. Cuối ngày: `get_chart_data(date=..., revision="origin")` để so kế hoạch gốc với nến thật, rồi `save_review`.

Kịch bản: `points` có ít nhất 2 điểm, thời gian tăng dần. `t` là epoch ms, `'YYYY-MM-DD HH:MM'` giờ VN, hoặc offset `{bars, tf}` tính từ nến đã đóng cuối lúc lưu. Đổi trạng thái một kịch bản đã có: `save_scenarios(symbol, [{code, status}], replace=false)`.

## Tài khoản và quyền

Tài khoản nằm trong bảng `dashboard_users`. Mật khẩu hash argon2, không dùng chung với admin: phiên admin không vào được dashboard và ngược lại.

| Quyền | `owner` | `viewer` |
|---|---|---|
| Xem Live, Review, tab Thống kê | có | có |
| Lưu kết quả review | có | không (API 403) |
| Vị thế mở, PnL, lệnh OKX | có | không (API 403, `/api/chart` không có `positions`, timeline bỏ `UNPLANNED_POSITION` và giá khớp) |
| Ghi chú: `note` của setup, ghi chú review, `note` của điểm kịch bản | có | không (API không trả) |

Viewer vẫn đọc được `summary`, `action_zone` của bias và `label`, `condition` của kịch bản, nên không ghi số vị thế hay PnL vào các chỗ đó. `notes` của `save_bias` không gửi xuống trình duyệt với mọi tài khoản.

### Mất quyền vào admin

Tạo lại owner bằng lệnh dự phòng:

```bash
docker exec -it bamcp bamcp dashboard create-owner <username>   # ngoài Docker: python bamcp_cli.py dashboard ...
```

Username đã có thì lệnh khôi phục thành owner đang hoạt động, đặt lại mật khẩu và đăng xuất mọi phiên của tài khoản đó.

### Phiên đăng nhập

- Cookie `HttpOnly; Secure; SameSite=Lax`, hết hạn trượt `session_hours`, tick "Ghi nhớ" thì `remember_days`. DB chỉ lưu SHA-256 của token.
- Sai liên tiếp `login_max_attempts` lần theo IP thì khoá `login_lockout_minutes` phút. IP lấy từ `X-Forwarded-For` do proxy đặt (`server.behind_proxy`), nên proxy phải ghi đè header này từ client. Traefik và Caddy mặc định đã làm vậy.
- Cookie `Secure` nghĩa là chạy thử chỉ đăng nhập được qua HTTPS hoặc `http://localhost`, không qua IP LAN.

## Review

Mặc định Review vẽ **kế hoạch gốc**: mỗi phần (bias, setup, kịch bản, TR) lấy bản đầu tiên có nội dung trong ngày. Lý do: quy trình chuẩn là `save_bias` trước, nên revision 1 thường chỉ có bias. Dropdown vẫn chọn được đúng từng revision, kể cả R1. Nến luôn là thật, vẽ tới hết ngày bias + 24h.

**`auto_outcome`** được tính lần đầu khi đọc setup đã kết thúc, từ nến M5 sau `TRIGGERED` đầu tiên qua được confirm: chạm TP trước là `tp_hit`, chạm SL trước hoặc cùng nến là `sl_hit`. Setup `FILLED` chưa chạm mốc nào thì để trống và tính lại lần sau. Kho không còn nến từ lúc trigger thì không kết luận.

**`save_review`** ghi đè `outcome`, không đụng `auto_outcome`. Khi lưu, `tp_hit` chuyển kịch bản active gắn với setup sang `played_out`; `sl_hit`, `invalidated` sang `failed`.

**Thống kê.** Win rate = thắng / lệnh đã đóng (`tp_hit`, `sl_hit`, `be`, `manual_exit`). Thắng là `tp_hit`, hoặc `be` / `manual_exit` có R dương. R trung bình chỉ tính setup có `r_multiple`.

## Cấu hình

Khối `dashboard` trong `config.yaml` là giá trị khởi tạo. Sửa ở `/admin/dashboard-accounts` thì ghi vào `dashboard/config.json` và có hiệu lực ngay.

- `enabled: false`: mọi route trả 404 và tool không trả link.
- `hide_pnl: true`: số PnL không xuống trình duyệt, kể cả owner.

Nến đang chạy: kho SQLite không lưu nến chưa đóng nên server ghép từ các nến M5 đã đóng, trễ tối đa một nến M5. Khung 5M vì thế không có nến đang chạy.

Biểu đồ dùng TradingView Lightweight Charts 4.2.3 (Apache 2.0), tự host trong `static/vendor/`.

## API của trang

Tất cả dưới `/setup-dashboard/api`, cần phiên, trả JSON, dùng chung hàm với tool MCP. `POST` cần header `X-CSRF-Token`.

| Method + path | Dùng cho | Quyền |
|---|---|---|
| `GET /symbols` | Danh sách cặp | mọi tài khoản |
| `GET /chart?symbol&tf&bars&date&revision&include_forming` | Toàn bộ dữ liệu biểu đồ, cache tới khi có nến mới hoặc kế hoạch đổi | mọi tài khoản |
| `GET /setups?symbol&date` | Thẻ setup và review | mọi tài khoản |
| `GET /events?symbol&date` | Timeline | mọi tài khoản |
| `GET /snapshots?symbol&date` | Danh sách revision | mọi tài khoản |
| `GET /review-stats?symbol&from&to&group_by` | Tab Thống kê | mọi tài khoản |
| `GET /positions?symbol` | Vị thế mở, gọi sàn cache 30 giây | owner |
| `GET /fills?symbol&date` | Lệnh OKX trong ngày, `setup_code` rỗng là lệnh không gắn setup | owner |
| `POST /reviews` | Lưu review `{setup_code, outcome, r_multiple, note}` | owner |
