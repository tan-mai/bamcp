# Deploy

[← README](../README.md)

Hướng dẫn này dành cho VPS không có quyền root, chỉ tạo container được qua giao diện (Hostinger). Không cần Docker trên máy cá nhân, không cần SSH vào VPS. Có shell thì xem [cách khác](#cách-khác).

```
GitHub Actions → ghcr.io → panel kéo image
domain.com → DNS → IP VPS → cổng 80/443 → Traefik → đọc Host header → container bamcp
```

## 1. Đẩy code lên GitHub

Trước khi push, `git status` **không được** hiện `.env`, `data/`, `.venv/`. Thấy `.env` là dừng lại, vì nó có thể chứa key sàn thật.

```bash
git status --short
git remote set-url origin https://github.com/<user>/bamcp.git   # repo của bạn
git push -u origin main
```

[publish.yml](../.github/workflows/publish.yml) tự chạy, build image và đẩy lên `ghcr.io/<user>/bamcp:latest`. Xem tiến trình ở tab **Actions**. Commit chỉ đổi `.md`, `.env.example` hay file compose thì không kích hoạt build. Khi đó bấm **Run workflow** nếu cần.

## 2. Mở quyền đọc cho image

Package trên ghcr mặc định private, mà VPS không có shell để `docker login`. Vào `github.com/users/<user>/packages` → chọn `bamcp` → **Package settings** → **Change visibility** → Public.

Repo vẫn để private được, GHCR tách riêng hai thứ. Image không chứa secret nào (`.env` bị `.dockerignore` loại, `config.yaml` có `password: ""`). Thứ lộ ra chỉ là code và bộ rule giao dịch.

## 3. Tạo container trong panel

Có hai cách, chọn một.

**Cách A: dán URL compose.** Trong repo của bạn, sửa domain và tên image trong [docker-compose.prod.yml](../docker-compose.prod.yml) (đang là `bamcp.online` và `ghcr.io/tan-mai/bamcp`). Dán URL raw của file đó vào ô URL trong Docker Manager. URL raw chỉ đọc được khi repo public. Sau khi container chạy, nối nó vào mạng của Traefik ở mục "Mạng bên ngoài". File này không đặt mật khẩu, nên lấy [setup token](admin.md#đặt-mật-khẩu-lần-đầu) trong log để đặt lần đầu. Làm xong thì sang [bước 6](#6-kiểm-tra).

**Cách B: tạo tay.**

| Ô | Giá trị |
|---|---|
| Container name | `bamcp` |
| Image | `ghcr.io/<user>/bamcp:latest` |
| Restart policy | `unless-stopped` |
| Ports | **để trống**, Traefik gọi thẳng vào container |
| Volumes | `bamcp_data:/data` |
| Mạng | chọn mạng của Traefik |

**Volume phải là named volume, không phải host path.** Không có shell thì không `chown` được thư mục trên host, mà container chạy uid 10001. Named volume thì Docker chép quyền sở hữu từ image sang nên ghi được ngay. `Dockerfile` đã `chown -R bamcp:bamcp /data` trước dòng `USER bamcp` đúng vì lý do này.

## 4. Biến môi trường

| Biến | Giá trị | Ghi chú |
|---|---|---|
| `BAMCP_ALLOWED_HOSTS` | `btc.domain.com,btc.domain.com:*` | **Bắt buộc** để `/mcp` chạy được |
| `BAMCP_USERNAME` | Tên đăng nhập bạn chọn | Không đặt thì dùng setup token |
| `BAMCP_PASSWORD` | Chuỗi ngẫu nhiên, **tối thiểu 8 ký tự** | Không đặt thì dùng setup token |
| `TZ` | `Asia/Ho_Chi_Minh` | Nên đặt |

**Credential sàn không đặt ở đây.** Sau khi container chạy, vào `/admin` để dán key. Key không nằm trong ô config của panel, và đổi sàn về sau không phải sửa container. Bỏ trống các biến `BAMCP_EXCHANGE*` thì phần [đọc sàn](account.md) tắt cho tới khi bạn cấu hình.

`BAMCP_ALLOWED_HOSTS` là chỗ dễ sập nhất: Traefik chuyển tiếp nguyên Host header, thiếu domain thật trong danh sách là `/mcp` trả **421**.

## 5. Nhãn Traefik

```
traefik.enable=true
traefik.http.routers.bamcp.rule=Host(`btc.domain.com`)
traefik.http.routers.bamcp.entrypoints=websecure
traefik.http.routers.bamcp.tls.certresolver=<tên resolver trên VPS>
traefik.http.services.bamcp.loadbalancer.server.port=8848
```

Dòng cuối bắt buộc, vì container mở cổng 8848 và Traefik không tự đoán được.

## 6. Kiểm tra

Log container phải có:

```
BAMCP data_root=/data symbol=BTCUSDT market=futures fetcher=True
INFO:     Uvicorn running on http://0.0.0.0:8848
```

Khi đã cấu hình sàn, log có thêm dòng `BAMCP account: okx (read-only)`.

Từ máy bất kỳ, chạy ba lệnh sau (PowerShell, trên Linux đổi `curl.exe` thành `curl` và `NUL` thành `/dev/null`):

```powershell
curl.exe -s https://btc.domain.com/healthz
curl.exe -s -o NUL -w "%{http_code}" -u "sai:sai" -X POST https://btc.domain.com/mcp
curl.exe -s -o NUL -w "%{http_code}" -u "<username>:<mật khẩu>" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d "{}" https://btc.domain.com/mcp
```

| Lệnh | Kết quả đúng | Nếu sai |
|---|---|---|
| Thứ nhất | `{"status":"ok",...}` | Kiểm tra DNS và nhãn Traefik |
| Thứ hai | `401` | Auth chưa hoạt động |
| Thứ ba | `400` | `421` nghĩa là `BAMCP_ALLOWED_HOSTS` thiếu domain. `401` nghĩa là sai login |

Phải dùng login đúng ở lệnh thứ ba, vì auth chạy trước kiểm tra Host: login sai luôn ra 401 dù thiếu domain. Lệnh này cố ý gửi body rỗng nên `400` là đúng, nghĩa là request đã vào tới MCP.

`probe.py` nằm sẵn trong image, nên nếu panel cho chạy lệnh trong container:

```bash
python probe.py get_positions
```

## 7. Việc cần làm sau deploy

1. Vào `/admin`: đặt mật khẩu nếu chưa có, thêm cặp, [dán key sàn](account.md#bật-đọc-tài-khoản) nếu cần.
2. [Gắn vào Claude](../README.md#gắn-vào-claude).
3. Ở `/admin/alerts`: [cấu hình thông báo](setups.md#thiết-lập-sau-khi-deploy).
4. Ở `/admin/dashboard-accounts`: [tạo tài khoản dashboard](dashboard.md#bắt-đầu).
5. [Backfill 5m](data.md#lịch-kéo-nến) và [chuyển sang SQLite](data.md#lưu-nến-trong-sqlite) nếu muốn giữ lịch sử dài.
6. Order flow: chạy thêm container / service `bamcp-orderflow` (đã có trong compose, [chi tiết](orderflow.md#cài-và-chạy)), rồi [backfill lệnh khớp](orderflow.md#backfill-lệnh-khớp) ở `/admin/orderflow`.

## 8. Cập nhật về sau

`git push` → Actions build lại image → vào panel bấm recreate container. Dữ liệu trong `bamcp_data` không mất.

Với tag `:latest`, recreate có thể vẫn chạy image cũ nếu Docker không kéo lại. `docker-compose.prod.yml` đã đặt `pull_policy: always` cho việc này. Tạo tay thì kiểm tra panel có kéo image mới không.

## Không thấy mục Request headers

Tài khoản Claude chưa được mở beta đó. Phương án tạm: đặt `auth.enabled: false`, đổi `mcp_path` thành một chuỗi ngẫu nhiên dài (`/mcp-a7f3...`), và coi URL đó như mật khẩu.

> **Rủi ro:** `auth.enabled: false` tắt luôn form đăng nhập của `/admin`. Bất kỳ ai cũng mở được các trang admin và gọi được action của chúng mà không cần đăng nhập. Nếu buộc phải dùng, hãy chặn đường `/admin` ở Traefik và bật lại auth sớm nhất có thể.

## Ba chỗ dễ sập

**Host header.** Traefik chuyển tiếp nguyên Host header của request. `BAMCP_ALLOWED_HOSTS` phải chứa domain thật, nếu không `/mcp` trả 421. Các đường khác như `/healthz` và `/admin` vẫn trả bình thường nên dễ tưởng server không sao. Dạng `host:*` mới khớp mọi port, riêng `"*"` không phải wildcard.

**IP của VPS.** Binance chặn một số vùng (đáng chú ý: VPS đặt ở Mỹ) và trả `HTTP 451`. Nếu `list_timeframes` báo `last_fetch_error` ngay sau khi khởi động, kiểm tra bằng `curl -I https://fapi.binance.com/fapi/v1/ping` từ chính VPS đó trước khi nghi ngờ code.

**Volume.** Container chạy uid 10001. Dùng **named volume** (`bamcp_data:/data`), đừng bind mount vào thư mục host. Không có shell thì không `chown` được, và `save_bias` sẽ lỗi permission ngay lần ghi đầu.

## Cách khác

**Có shell, dùng Docker Compose:** tạo `.env` từ `.env.example`, sửa tên mạng và domain trong [docker-compose.yml](../docker-compose.yml), rồi `docker compose up -d --build`.

**Cài thẳng lên máy, không Docker:** [bamcp.service](../bamcp.service) là bản systemd. Khi đó nhớ đổi `server.host` về `127.0.0.1` trong `config.yaml`.
