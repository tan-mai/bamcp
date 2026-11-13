# Trang admin và bảo mật

[← README](../README.md)

`/admin` là nơi đặt mật khẩu API, credential sàn, cặp theo dõi và quy định bằng giao diện. Mọi thứ lưu xuống `<data_root>/settings.json`, nằm trong volume nên **sống sót qua mọi lần tạo lại container**. Khi deploy bằng panel không có SSH, bạn không phải nhập lại secret mỗi lần recreate.

## Đặt mật khẩu lần đầu

Không có tài khoản mặc định nào trong mã nguồn. Mật khẩu đầu tiên vào bằng một trong hai đường.

**Cách khuyên dùng: biến môi trường lúc tạo container.** Container khởi động là đăng nhập được ngay, không có cửa sổ nào để hở.

```
BAMCP_USERNAME=<tên bạn chọn>
BAMCP_PASSWORD=<chuỗi ngẫu nhiên, tối thiểu 8 ký tự>
```

**Cách dự phòng: setup token.** Nếu không đặt hai biến trên, hoặc mật khẩu ngắn hơn 8 ký tự, server vẫn chạy và in token ra log:

```
BAMCP CHUA DUOC CAI DAT - chua co mat khau nao.
  BAMCP SETUP TOKEN: y-rpoLBrJySnC4rluqovvZXtV1fJmQ8-9MHrzQkOWJk
```

Lúc này các trang `/admin` mở công khai nhưng **không ghi được gì nếu thiếu token**, và `/mcp` vẫn trả 401. Token giữ nguyên qua các lần khởi động lại và bị xoá ngay khi đặt xong mật khẩu.

Mật khẩu ngắn còn để lại một dòng trong log: `CANH BAO: bo qua BAMCP_PASSWORD - mat khau phai tu 8 ky tu tro len`.

## Các trang

Thanh trên cùng của mọi trang admin dẫn tới:

| Trang | Đường dẫn | Tài liệu |
|---|---|---|
| Cài đặt | `/admin` | Trang này |
| Phiên ORB | `/admin/orb` | [orb.md](orb.md) |
| Gann | `/admin/gann` | [gann.md](gann.md) |
| Kéo nến | `/admin/schedules` | [data.md](data.md#lịch-kéo-nến) |
| Vào lệnh | `/admin/alerts` | [setups.md](setups.md) |
| Dashboard accounts | `/admin/dashboard-accounts` | [dashboard.md](dashboard.md) |

Phiên ORB và Gann chỉ hiện khi module tương ứng bật (`orb.enabled`, `time_windows.enabled`).

## Trang Cài đặt

Chia hai cột, bên trái là kết nối, bên phải là quy định:

1. **Truy cập API**: đổi username/mật khẩu về sau.
2. **Tài khoản sàn**: chọn binance/bybit/okx, dán API key read-only, bấm *Kiểm tra kết nối sàn* để xác nhận ngay tại chỗ.
3. **Cặp giao dịch**: thêm / tắt / bỏ cặp theo dõi. Các nút này có hiệu lực ngay khi bấm, không chờ Lưu.
4. **Quy định chung**: 4 rule áp cho cả tài khoản (quota, margin, margin swing, dừng ngày).
5. **Quy định theo từng cặp**: 5 ngưỡng riêng của từng cặp (SL/TP theo điểm giá, ngưỡng swing, R:R).
6. **Lý do & lịch sử**: ô lý do (bắt buộc khi đổi rule), kèm lịch sử 5 lần đổi gần nhất. Xem [rules.md](rules.md#trên-trang-admin).

Nút **Lưu thay đổi** nằm trên thanh dính ở đáy màn hình, bấm một lần là mục 1, 2, 4, 5 cùng ghi. Mọi thay đổi có hiệu lực ngay, không cần khởi động lại.

**Kiểm tra kết nối sàn** gọi thật lên sàn bằng credential đang lưu, chỉ đọc vị thế. Ví dụ kết quả:

```
Ket noi okx thanh cong - 1 vi the dang mo (BTC-USDT-SWAP).
```

## Admin và Claude làm được gì

Hai đường này cùng đọc ghi một chỗ:

| | Trang admin | Claude |
|---|---|---|
| Mật khẩu API | có | không |
| Credential sàn | có | không |
| Quy định giao dịch | có | `get_rules` / `update_rules` |
| Xem lịch sử đổi rule | có | `get_rules` |

Mật khẩu và credential sàn cố ý **không** cho Claude sửa. Chúng là thứ bảo vệ chính Claude, không nên nằm trong tầm với của nó.

## Thứ tự ưu tiên

```
settings.json  >  biến môi trường  >  config.yaml
```

Biến môi trường chỉ là **giá trị khởi tạo** cho lần chạy đầu. Sau khi bấm Lưu, sửa `.env` không còn tác dụng. Nếu không thì bấm Lưu xong mà không thấy gì đổi, kiểu lỗi khó hiểu nhất.

## Cách giữ secret

- **Mật khẩu API lưu dạng hash** PBKDF2-SHA256, 200k vòng, salt riêng. Không có chỗ nào lưu plaintext.
- **Secret của sàn buộc phải lưu đọc lại được**, vì cần nó để ký request mà server phải tự khởi động lại không có người gõ tay. File `chmod 600`. Không có cách nào tránh, và mã hoá tại chỗ chỉ là hình thức vì khoá giải mã cũng phải nằm cạnh.
- **Server không bao giờ gửi secret về trình duyệt.** Trang chỉ nhận "đã có / chưa có" và 4 ký tự cuối của API key: đủ để đối chiếu với trang sàn, không đủ để dùng lại.
- **Ô để trống = giữ nguyên.** Muốn xoá thì xoá ở `settings.json`.

## Xác thực

`/mcp` dùng HTTP Basic, còn `/admin` dùng form đăng nhập tại `/admin/login`. Hai bên chung một username và mật khẩu, so khớp với hash trong `settings.json`. `BAMCP_USERNAME` / `BAMCP_PASSWORD`, rồi tới `server.auth` trong `config.yaml`, chỉ được nạp khi `settings.json` chưa có mật khẩu. So sánh credential dùng `secrets.compare_digest` cho cả hai vế, không short-circuit.

`/admin` **không nhận Basic auth**, và cookie phiên của `/admin` cũng không mở được `/mcp`.

Cách phiên đăng nhập `/admin` hoạt động:

- **Hạn phiên** tính từ lúc đăng nhập, không tự gia hạn: `server.auth.session_hours` (mặc định 12 giờ), hoặc `remember_days` (30 ngày) nếu tick *Ghi nhớ*.
- **Đổi mật khẩu** làm mọi phiên cũ hết hiệu lực ngay. **Đăng xuất** xoá phiên ở phía server.
- **Khoá theo IP** sau `login_max_attempts` lần sai liên tiếp (mặc định 5), trong `login_lockout_minutes` phút (mặc định 15). Bộ đếm tách riêng với dashboard. Basic auth của `/mcp` không có khoá này.
- **Cookie** `bamcp_admin` có `HttpOnly`, `SameSite=Lax`, chỉ gửi trong `/admin`, và có `Secure` khi truy cập qua HTTPS. Reverse proxy phải chuyển tiếp `X-Forwarded-Proto` (Traefik và Caddy làm sẵn), nếu không cookie sẽ thiếu `Secure`. Token trong DB chỉ lưu dạng SHA-256.
- **Chống CSRF:** mọi `POST` trong `/admin` phải có `Content-Type: application/json`, trừ đăng nhập và đăng xuất.

Hết phiên giữa chừng thì nút bấm báo lỗi và dữ liệu đang nhập vẫn còn trên trang. Đăng nhập lại ở tab khác rồi bấm lại là được.

`/healthz` không cần auth. `/setup-dashboard` không dùng cơ chế này vì có [đăng nhập riêng](dashboard.md#tài-khoản-và-quyền).

Thiếu credential thì server không dừng mà vào chế độ [setup token](#đặt-mật-khẩu-lần-đầu).

> **Chỉ đặt `auth.enabled: false` khi test local.** Khi đó server bỏ hẳn xác thực: `/mcp`, mọi trang `/admin` và các action của chúng đều không còn yêu cầu đăng nhập.

## Bề mặt HTTP

| Endpoint | Auth | Việc |
|---|---|---|
| `POST /mcp` | Basic | Giao thức MCP, Claude nói chuyện ở đây |
| `GET /healthz` | không | Health check cho Docker HEALTHCHECK và reverse proxy |
| `GET, POST /admin/login` | không | Form đăng nhập admin, khoá theo IP khi sai nhiều lần |
| `POST /admin/logout` | phiên | Xoá phiên admin |
| `GET /admin` | có* | Trang Cài đặt |
| `POST /admin/save`, `/admin/test`, `/admin/symbols` | có* | Lưu cài đặt, kiểm tra kết nối sàn, thêm/tắt/bỏ cặp |
| `GET /admin/orb` | có* | Quản lý phiên, rule ORB và news days (chỉ có khi `orb.enabled`) |
| `POST /admin/orb/sessions` | có* | Xem trước / lưu / bật / tắt / xoá phiên |
| `POST /admin/orb/rules` | có* | Xem trước / lưu rule ORB, thêm / xoá news day |
| `GET /admin/gann`, `POST /admin/gann/action` | có* | Cặp phân tích, pivot thủ công, sự kiện, config GANN |
| `GET /admin/schedules` | có* | Lịch kéo nến theo từng khung |
| `POST /admin/schedules/action` | có* | Thêm / sửa / bật tắt / xoá / chạy ngay / xem trước cron |
| `GET /admin/alerts` | có* | Thông báo Discord/Telegram, watcher, setups, mục Off-plan ([lệnh ngoài kế hoạch](off-plan.md)) |
| `POST /admin/alerts/action` | có* | Lưu thông báo / gửi thử / lưu watcher / lưu off-plan / huỷ, tạo setup, timeline |
| `GET /admin/dashboard-accounts` | có* | Tài khoản, cấu hình, thang điểm xác suất TR, audit log và lịch sử đăng nhập của dashboard |
| `POST /admin/dashboard-accounts/action` | có* | Tạo / sửa / bật tắt / xoá tài khoản, lưu cấu hình |
| `GET, POST /setup-dashboard/login` | không | Form đăng nhập dashboard, khoá theo IP khi sai nhiều lần |
| `POST /setup-dashboard/logout` | phiên | Xoá phiên |
| `GET /setup-dashboard` | phiên | Trang dashboard |
| `/setup-dashboard/static/*` | phiên | CSS và JS của trang |
| `/setup-dashboard/api/*` | phiên | [API JSON](dashboard.md#api-của-trang) cho trang |

`*` Cần phiên đăng nhập admin: chưa đăng nhập thì `GET` chuyển về `/admin/login`, `POST` trả 401. Các trang `/admin` mở công khai khi **chưa** đặt mật khẩu lần nào, nhưng lúc đó phải có setup token mới ghi được.

Ngoài API của dashboard thì không có REST API nào khác. Muốn pull dữ liệu ngay thì bảo Claude gọi tool `refresh_data`.
