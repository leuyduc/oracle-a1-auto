# Săn Oracle A1 Always Free tự động — Singapore West (AD-1)

**Mục tiêu:** tự thử tạo đúng 1 máy `VPS-A`: VM.Standard.A1.Flex, 1 OCPU, 6 GB RAM, Ubuntu 24.04 ARM64, boot volume 50 GB, public IPv4. Không thuê VPS, không dùng máy ở nhà, không phải bấm Create liên tục.

Phương pháp: GitHub Actions chạy **12 lần/ngày** (2 giờ/lần); mỗi lượt kiểm tra đã có A1 hay chưa, nếu chưa thì thử tạo 1 lần. RAM 6 GB có thể khó được cấp hơn 2 GB khi tài nguyên khan hiếm, nhưng phù hợp hơn để chạy Hermes lâu dài. Khi Oracle trả `Out of capacity`, kết thúc và thử ở lượt sau. Nếu Oracle tạo thành công, các lần tiếp theo tự bỏ qua. **Chương trình không tự động tắt lịch trong GitHub; nên bấm Disable workflow khi đã có máy.**

## 1) Kiểm tra mạng Oracle (bắt buộc)

Mở Oracle Cloud Console, kiểm tra **góc phải: Singapore West**.

Vào **Networking → Virtual Cloud Networks**. Nếu trước đó tạo VPS lỗi, VCN/subnet trong trình tạo instance **có thể chưa được lưu**. Nếu chưa thấy mạng có public subnet: chọn **Start VCN Wizard → Create VCN with Internet Connectivity** → hoàn tất. Chỉ dùng 1 VCN / 1 public subnet, không thêm NAT hoặc dịch vụ trả phí tùy chọn ngoài wizard nếu không cần.

Vào VCN → **Subnets** → chọn **public subnet** → copy **OCID** bắt đầu bằng `ocid1.subnet.`. Đừng lấy OCID của VCN.

## 2) Tạo API signing key riêng (KHÁC SSH key đã tải)

Oracle Console → góc phải **Profile → User settings / My profile → API keys → Add API key → Generate API key pair**.

Tải **API private key .pem** về máy, sao chép toàn bộ **Configuration File Preview** gồm `[DEFAULT]`, `user=`, `fingerprint=`, `tenancy=`, `region=`, `key_file=`. Region phải là `ap-singapore-2`. KHÔNG dùng SSH private key làm API key.

Giữ API private key kín; không dán trong chat, commit, tệp README hoặc gửi cho người khác.

## 3) Tạo GitHub repo riêng tư và tải mã lên

Tạo repository `oracle-a1-auto` với tùy chọn **Private**, mặc định branch `main`.

Trong repo, tải lên **đúng hai tệp** cùng đường dẫn:

- `try_a1.py` (ở gốc repo)
- `.github/workflows/oracle-a1-auto.yml` (cần giữ nguyên đường dẫn có thư mục `.github`)

Cách đơn giản trên Windows nếu quen GitHub Desktop / VS Code: clone repo, copy 2 tệp từ ZIP vào đúng chỗ, commit và push. Hoặc GitHub Web: **Add file → Create new file**, nhập tên tệp gồm đường dẫn rồi dán nội dung từ ZIP.

## 4) Thêm bốn GitHub Actions secrets (không đưa vào code)

Mở repo → **Settings → Secrets and variables → Actions → New repository secret**. Tạo:

| Secret | Nội dung | Ví dụ |
|---|---|---|
| `OCI_CONFIG_TEXT` | Nguyên văn Configuration File Preview của API key Oracle | `[DEFAULT] ... region=ap-singapore-2` |
| `OCI_PRIVATE_KEY` | Nội dung đầy đủ file **API** private key `.pem` | `-----BEGIN PRIVATE KEY----- ...` |
| `OCI_SSH_PUBLIC_KEY` | Nội dung 1 dòng file **SSH public key** đã tải lúc tạo VPS | `ssh-rsa AAAA...` |
| `OCI_SUBNET_OCID` | OCID của public subnet ở bước 1 | `ocid1.subnet.oc1.ap-singapore-2...` |

`key_file` trong `OCI_CONFIG_TEXT` được script bỏ qua và thay bằng file tạm từ `OCI_PRIVATE_KEY`. Không cần sửa đường dẫn Windows trong config preview.

**Không** thêm API private key vào repository hoặc gửi lên các dịch vụ lạ. Dù GitHub Secrets mã hóa lúc lưu trữ, cần coi đó là quyền truy cập tài khoản Oracle. Ưu tiên API user có quyền hạn tối thiểu; không chia sẻ quyền chỉnh sửa repository.

## 5) Chạy kiểm thử 1 lần rồi để tự chạy

GitHub repo → **Actions → Oracle A1 Free - Auto Retry (Singapore West) → Run workflow**. Xem log:

- `NO_CAPACITY` → hợp lệ, tiếp tục thử theo lịch (không cần làm gì).
- `INSTANCE_CREATED` → Oracle đã nhận yêu cầu tạo máy. Vào **Compute → Instances** đợi trạng thái `RUNNING`, lấy Public IP. **Vào Actions → workflow → Disable workflow** để dừng các lượt tiếp theo.
- `ALREADY_EXISTS` → có A1.Flex trong root compartment, không tạo máy trùng.
- `SETUP_ERROR` → thiếu/mismatch key, subnet, hoặc không tìm được Ubuntu ARM64; cần xử lý lỗi, không cố tạo liên tục.
- `ORACLE_ERROR` → lỗi Oracle khác tình trạng hết tài nguyên, xem mã lỗi.

GitHub schedule có thể chạy muộn hơn thời điểm dự kiến; không bảo đảm bắt được máy khi nó trống trong thời gian ngắn. Một số tháng có thể không có tài nguyên.

## 6) Kiểm soát chi phí và bảo mật

- Chỉ tạo shape **A1.Flex 1 OCPU / 6 GB**, boot volume **50 GB**, không tạo thêm block volume.
- Hạn mức A1 Always Free tính đến 18/11/2026: **tổng 2 OCPU / 12 GB RAM**; vùng Home Region Singapore West. Hạn mức boot/block storage 200 GB dùng chung.
- GitHub Free hiện có **$12 hạn mức compute usage mỗi tháng** dùng chung cho các dịch vụ tương ứng. Với lịch 12 lượt/ngày, timeout mỗi lượt 2 phút => tối đa khoảng 720 phút cho 30 ngày (tương đương khoảng $4,32 theo đơn giá standard Linux $0,006/phút), chưa tính các tác vụ GitHub khác, nên thường nằm trong hạn mức miễn phí. Để chắc chắn không phát sinh hóa đơn: kiểm tra **Settings → Billing and licensing → Budgets** và bật **Stop usage when budget limit is reached**; không cấu hình phương thức thanh toán nếu không cần.
- Nên dùng user Oracle với IAM policy tối thiểu cho API key; giữ repo Private, không cho người lạ có quyền push workflow.
- **Sau khi VPS tạo thành công:** vào GitHub Actions → Disable workflow, rồi **xóa API signing key Oracle** nếu không dùng nữa, vì nó là khóa truy cập tài khoản có thể bị lạm dụng.
- **Không** chạy cùng lúc nhiều chương trình săn VPS khác nhau trên cùng tài khoản. Bộ này kiểm tra A1 đang tồn tại trước mỗi lượt nhưng không thể ngăn một chương trình bên ngoài tạo đúng lúc.
- VCN/subnet phải có tuyến Internet Gateway và luật SSH 22 phù hợp; nên giới hạn nguồn SSH chỉ IP của anh. Trên Oracle có thể phải cấu hình rule cổng 22 trong Security List/NSG sau khi tạo máy.
- VPS Always Free ít hoạt động có nguy cơ Oracle thu hồi; cần sao lưu dữ liệu Hermes định kỳ.

## Tài liệu chính thức

- https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/launchinginstance.htm
- https://docs.oracle.com/en-us/iaas/Content/Compute/known-issues.htm
- https://docs.oracle.com/en-us/iaas/Content/Identity/Tasks/managingcredentials.htm
- https://docs.github.com/en/actions/reference/events-that-trigger-workflows#schedule
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets
- https://docs.github.com/en/billing/reference/product-usage-included

**Ghi chú:** Đây là bộ mã mẫu đã kiểm tra cú pháp, chưa chạy thử trên tài khoản Oracle của anh. Không có quyền truy cập Oracle của anh nên không thể xác nhận sẽ được cấp tài nguyên.
