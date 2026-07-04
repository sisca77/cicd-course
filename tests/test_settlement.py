"""
이커머스 정산 시스템 - pytest 테스트 스위트
[AI 활용 CI/CD 교육] Day 1 · Part 4

실행:
  pytest tests/ -v --cov=settlement --cov-report=term-missing

AI 활용 포인트:
  이 파일을 Claude.ai에 붙여넣고 "테스트 케이스를 보강해줘" 라고 물어보세요
"""
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from settlement.main import app
from settlement.models.models import Order, OrderStatus, SettlementStatus
from settlement.services.settlement_service import SettlementService

# ── 픽스처 ────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def svc():
    return SettlementService()


@pytest.fixture
def sample_order():
    return Order(
        order_id=f"TEST-{uuid.uuid4().hex[:6]}",
        merchant_id="M-TEST",
        customer_id="C-001",
        amount=Decimal("100000"),
        fee_rate=Decimal("0.03"),
    )


# ── 모델 단위 테스트 ──────────────────────────────────────────────────

class TestOrderModel:
    def test_fee_amount(self):
        """수수료 3% 계산"""
        o = Order(order_id="T1", merchant_id="M", customer_id="C",
                  amount=Decimal("100000"))
        assert o.fee_amount == Decimal("3000")   # 100,000 × 3%

    def test_net_amount(self):
        """실 정산액 = 매출 - 수수료"""
        o = Order(order_id="T2", merchant_id="M", customer_id="C",
                  amount=Decimal("100000"))
        assert o.net_amount == Decimal("97000")

    def test_default_status_pending(self):
        o = Order(order_id="T3", merchant_id="M", customer_id="C",
                  amount=Decimal("50000"))
        assert o.status == OrderStatus.PENDING

    def test_negative_amount_raises(self):
        with pytest.raises(Exception):
            Order(order_id="T4", merchant_id="M", customer_id="C",
                  amount=Decimal("-1"))

    def test_fee_rounding(self):
        """소수점 수수료 반올림 (원 단위)"""
        o = Order(order_id="T5", merchant_id="M", customer_id="C",
                  amount=Decimal("33333"), fee_rate=Decimal("0.03"))
        # 33333 × 0.03 = 999.99 → 1000 (반올림)
        assert o.fee_amount == Decimal("1000")


# ── 서비스 단위 테스트 ────────────────────────────────────────────────

# ── 서비스 단위 테스트 ────────────────────────────────────────────────

class TestSettlementService:
    def test_add_and_complete_order(self, svc, sample_order):
        svc.add_order(sample_order)
        done = svc.complete_order(sample_order.order_id)

        assert done is not None
        assert done.status == OrderStatus.COMPLETED
        assert done.completed_at is not None

    def test_complete_nonexistent_returns_none(self, svc):
        assert svc.complete_order("NONE-EXIST") is None

    def test_calculate_settlement_basic(self, svc):
        """3건 주문 정산 계산 기본 케이스"""
        merchant = "M-CALC"
        amounts = [Decimal("50000"), Decimal("100000"), Decimal("200000")]

        for i, amt in enumerate(amounts):
            o = Order(
                order_id=f"O-{i}",
                merchant_id=merchant,
                customer_id="C",
                amount=amt,
            )
            svc.add_order(o)
            svc.complete_order(o.order_id)

        start = datetime.utcnow() - timedelta(hours=1)
        end = datetime.utcnow() + timedelta(hours=1)
        rec = svc.calculate_settlement(merchant, start, end)

        expected_sales = sum(amounts)
        expected_fee = sum(a * Decimal("0.03") for a in amounts)

        assert rec.order_count == 3
        assert rec.total_sales == expected_sales
        assert rec.total_fee.quantize(Decimal("1")) == expected_fee.quantize(
            Decimal("1")
        )
        assert rec.net_amount == expected_sales - rec.total_fee
        assert rec.status == SettlementStatus.PENDING

    def test_pending_orders_excluded(self, svc):
        """PENDING 상태 주문은 정산 제외"""
        o = Order(
            order_id="PEND-1",
            merchant_id="M-X",
            customer_id="C",
            amount=Decimal("100000"),
        )
        svc.add_order(o)

        start = datetime.utcnow() - timedelta(hours=1)
        end = datetime.utcnow() + timedelta(hours=1)
        rec = svc.calculate_settlement("M-X", start, end)

        assert rec.order_count == 0
        assert rec.total_sales == Decimal("0")

    def test_process_settlement(self, svc, sample_order):
        svc.add_order(sample_order)
        svc.complete_order(sample_order.order_id)

        rec = svc.calculate_settlement(
            "M-TEST",
            datetime.utcnow() - timedelta(hours=1),
            datetime.utcnow() + timedelta(hours=1),
        )
        done = svc.process_settlement(rec.settlement_id)

        assert done.status == SettlementStatus.COMPLETED
        assert done.processed_at is not None

    def test_list_settlements_filter(self, svc):
        """merchant_id 필터 동작 확인"""
        for m in ["M-A", "M-B"]:
            o = Order(
                order_id=f"O-{m}",
                merchant_id=m,
                customer_id="C",
                amount=Decimal("10000"),
            )
            svc.add_order(o)
            svc.complete_order(o.order_id)
            svc.calculate_settlement(
                m,
                datetime.utcnow() - timedelta(hours=1),
                datetime.utcnow() + timedelta(hours=1),
            )

        result = svc.list_settlements(merchant_id="M-A")

        assert all(r.merchant_id == "M-A" for r in result)

    def test_list_settlements_filter_by_merchant_id_and_status(self, svc):
        """
        list_settlements()에서 merchant_id와 status 동시 필터링 확인
        """
        now = datetime.utcnow()
        start = now - timedelta(hours=1)
        end = now + timedelta(hours=1)

        # M-A: PENDING 정산
        order_a_pending = Order(
            order_id="O-MA-PENDING",
            merchant_id="M-A",
            customer_id="C",
            amount=Decimal("10000"),
        )
        svc.add_order(order_a_pending)
        svc.complete_order(order_a_pending.order_id)

        settlement_a_pending = svc.calculate_settlement("M-A", start, end)

        # M-A: COMPLETED 정산
        order_a_completed = Order(
            order_id="O-MA-COMPLETED",
            merchant_id="M-A",
            customer_id="C",
            amount=Decimal("20000"),
        )
        svc.add_order(order_a_completed)
        svc.complete_order(order_a_completed.order_id)

        settlement_a_completed = svc.calculate_settlement("M-A", start, end)
        svc.process_settlement(settlement_a_completed.settlement_id)

        # M-B: PENDING 정산
        order_b_pending = Order(
            order_id="O-MB-PENDING",
            merchant_id="M-B",
            customer_id="C",
            amount=Decimal("30000"),
        )
        svc.add_order(order_b_pending)
        svc.complete_order(order_b_pending.order_id)

        svc.calculate_settlement("M-B", start, end)

        result = svc.list_settlements(
            merchant_id="M-A",
            status=SettlementStatus.PENDING,
        )

        result_ids = {r.settlement_id for r in result}

        assert len(result) >= 1
        assert all(r.merchant_id == "M-A" for r in result)
        assert all(r.status == SettlementStatus.PENDING for r in result)
        assert settlement_a_pending.settlement_id in result_ids
        assert settlement_a_completed.settlement_id not in result_ids

    def test_process_settlement_nonexistent_returns_none(self, svc):
        """
        process_settlement()에서 존재하지 않는 settlement_id 조회 시 None 반환
        """
        result = svc.process_settlement("NONE-EXIST-SETTLEMENT")

        assert result is None

    def test_calculate_settlement_empty_period(self, svc):
        """
        calculate_settlement()에서 해당 기간 내 주문이 0건인 경우
        """
        merchant = "M-EMPTY"

        order = Order(
            order_id="O-EMPTY-OUTSIDE",
            merchant_id=merchant,
            customer_id="C",
            amount=Decimal("100000"),
        )
        svc.add_order(order)
        svc.complete_order(order.order_id)

        future_start = datetime.utcnow() + timedelta(days=10)
        future_end = datetime.utcnow() + timedelta(days=11)

        rec = svc.calculate_settlement(
            merchant,
            future_start,
            future_end,
        )

        assert rec.merchant_id == merchant
        assert rec.order_count == 0
        assert rec.total_sales == Decimal("0")
        assert rec.total_fee == Decimal("0")
        assert rec.net_amount == Decimal("0")
        assert rec.status == SettlementStatus.PENDING


# ── API 통합 테스트 ───────────────────────────────────────────────────

class TestAPI:
    def test_health(self, client):
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    def test_ready(self, client):
        res = client.get("/ready")
        assert res.status_code == 200

    def test_create_order(self, client):
        payload = {
            "order_id": f"API-{uuid.uuid4().hex[:6]}",
            "merchant_id": "M-API",
            "customer_id": "C-001",
            "amount": "75000",
            "fee_rate": "0.03",
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
        }
        res = client.post("/api/v1/orders", json=payload)
        assert res.status_code == 201
        assert res.json()["order_id"] == payload["order_id"]

    def test_list_settlements(self, client):
        res = client.get("/api/v1/settlements")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_list_settlements_filter(self, client):
        res = client.get("/api/v1/settlements?merchant_id=M-001")
        assert res.status_code == 200
