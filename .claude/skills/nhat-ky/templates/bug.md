---
ngay: {{ngay}}
gio: "{{gio}}"
loai: bug
tieu_de: "{{tieu_de}}"
trang_thai: dang-lam          # dang-lam | xong | treo
muc_do_nghiem_trong: ""       # nghiem-trong | trung-binh | nhe
muc_do_chac_chan: chua-verify # da-xac-minh | co-dau-hieu | chua-verify
nhanh: {{nhanh}}
commit_goc: {{commit}}
lien_quan: []
---

# {{tieu_de}}

## 1. Tóm tắt
<!-- 2-4 câu: sai cái gì, gốc rễ là gì, đã sửa chưa, đã có check chống tái phạm chưa. -->

## 2. Triệu chứng
<!-- Sai ở đâu, quan sát bằng cách nào, output/log NGUYÊN VĂN, phát hiện lúc nào. -->

## 3. Cách tái hiện
<!-- Lệnh/bước tối thiểu. Tái hiện ổn định hay thất thường? -->

## 4. Phạm vi ảnh hưởng
<!-- Dữ liệu / component nào, từ khi nào, bao nhiêu. Đo bằng số, kèm lệnh đo (vd "5/13 kênh, 117 tin"). -->

## 5. Quá trình điều tra
<!-- Ghi cả hướng SAI và vì sao loại; đây là phần học được nhiều nhất. -->

| # | Giả thuyết | Kiểm chứng bằng | Kết quả |
|---|---|---|---|

## 6. Nguyên nhân gốc
<!-- Nguyên nhân trực tiếp khác nguyên nhân gốc. Bằng chứng. Vì sao bug sống được tới giờ mà không ai thấy. -->

## 7. Hệ quả
<!-- Đã gây ra gì; nếu không sửa sẽ gây ra gì ở downstream. -->

## 8. Cách sửa
<!-- Nêu ĐÚNG đường dẫn mọi file thêm/sửa/xóa (hook nhat_ky_guard kiểm tra). Vì sao sửa ở tầng này mà không ở tầng khác. -->

| File | Thay đổi | Lý do |
|---|---|---|

## 9. Phương án sửa khác và khi nào dùng
<!-- Ít nhất 2 phương án thật. -->

| Phương án | Ưu | Nhược | Hợp khi |
|---|---|---|---|

## 10. Trade-off của cách sửa
<!-- Được gì / mất gì. Phải có cái MẤT. -->

## 11. Bằng chứng đã sửa
<!-- Trước/sau, output NGUYÊN VĂN. Chưa verify thì ghi CHƯA VERIFY + lệnh cần chạy + chạy ở đâu. -->

## 12. Chống tái phạm
<!-- Test / preflight / check thực thi được ("bánh cóc": mỗi bug sửa xong để lại một check).
     Một dòng bài học trong markdown KHÔNG tính. Chưa có thì nêu lý do và đưa vào mục 15. -->

## 13. Lớp bug
<!-- Thuộc lớp nào trong docs/context/05_failure_patterns.md (vd "giá trị thiếu -> default im lặng")?
     Đã gặp ở đâu trước đây? Có cần bổ sung file 05 không? -->

## 14. Bài học
<!-- Giải thích để Pan HIỂU cơ chế, không chỉ nhớ quy tắc. -->

## 15. Việc còn mở
- [ ]

## 16. Liên kết
<!-- Commit, GHI_CHU_NGAYxx, nhật ký task đang làm khi phát hiện bug, tài liệu đã đọc. -->

## Tiến trình
- {{ngay}} {{gio}}: tạo nhật ký.
