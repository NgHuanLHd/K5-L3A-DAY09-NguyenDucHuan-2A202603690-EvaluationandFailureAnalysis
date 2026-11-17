# Trace case: từ trace tới quyết định sai đầu tiên

> **Đọc nhanh.** Lấy một lần chạy lỗi, đọc từng bước để tìm chỗ agent *bắt đầu* sai. Ở claim C-002, agent tra DB đúng (trễ 2 ngày — không được hoàn), nhưng ngay bước 3 lại lấy con số "5 ngày" trong tin nhắn khách đè lên DB, nên hoàn nhầm 20.000đ. Câu trả lời cho khách vẫn trôi chảy và khớp sổ cái, nên chỉ đọc reply sẽ không thấy lỗi. Đã viết test để lỗi này không quay lại.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11` (item E-002), run `E-seeded_regression-20261117-6a30` |
| Triệu chứng | Fail `pass_rate` (status resolved ≠ denied), `refund_amount_correct` (ledger 20.000 ≠ nhãn 0), `db_fact_kept`. Reply "đã hoàn 20.000đ" khớp ledger — reply nhất quán nhưng khoản hoàn sai. |
| Bước sai đầu tiên | Event **#3** `model_override` — `{"delay_days": 5}` |
| Vì sao là bước *đầu tiên* | #1 `handoff` đúng scope; #2 `investigator` đọc đúng DB `delay_days: 2`. #3 lấy "5 ngày" từ ticket đè lên DB. #4 `quote` 20.000đ (đúng ra `no_entitlement`) và các bước sau là hệ quả. |
| Failure mode | FM-03 ticket_trust |
| Đối chứng | `fixed C-002@11`: không có override, #3 `quote` 0 (`no_entitlement`), status denied, ledger 0. |
| Regression test | `src/tests/test_lab9_trace_case.py` (3 test, marker `student`, chạy trong `eval-gate`): override ở event #3 sau 2 bước đúng; `DbFactKept`/`RefundAmountCorrect` = False trên variant lỗi; True trên `fixed`. Item E-002 có `failure_modes: ["FM-03"]`. |
| Chưa chắc chắn | MockLLM tin ticket theo quy tắc cứng; với LLM thật không biết tần suất theo cách diễn đạt ticket. Trace không có event quyết định của host — "đã ghi 20.000đ" lấy từ ledger. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
