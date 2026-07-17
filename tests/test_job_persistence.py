"""Job/A2A Task 영속화 테스트 (Phase 6) — 재시작 생존을 실제로 증명한다.

'재시작'은 새 JobStore 인스턴스 생성으로 시뮬레이션한다(메모리 캐시가 빈 상태에서
같은 DB를 읽음) — 프로세스를 실제로 죽이지 않고 영속화 계약만 정확히 검증한다.
conftest가 A2A_DB_PATH를 tmp로 격리하므로 실 DB를 오염시키지 않는다.
"""
import json

import app.a2a as a2a_mod
from app.jobs import Job, JobStore
from app.schemas import JobStatus


class TestJobDurability:
    def test_completed_job_survives_restart(self):
        s1 = JobStore(reap=False)
        job, _ = s1.create()
        s1.run(job, lambda: {"answer": 42})
        assert job.status == JobStatus.done

        s2 = JobStore(reap=False)          # 재시작 — 메모리 캐시 없음
        restored = s2.get(job.job_id)
        assert restored is not None
        assert restored.status == JobStatus.done
        assert restored.result == {"answer": 42}
        assert restored.log.entries          # 최종 로그도 보존

    def test_unknown_job_still_none(self):
        assert JobStore(reap=False).get("없는job") is None

    def test_error_job_survives_restart(self):
        s1 = JobStore(reap=False)
        job, _ = s1.create()

        def boom():
            raise ValueError("터짐")
        s1.run(job, boom)

        s2 = JobStore(reap=False)
        restored = s2.get(job.job_id)
        assert restored.status == JobStatus.error
        assert restored.error["code"] == "internal"

    def test_idempotency_survives_restart(self):
        """client_request_id 멱등이 재시작을 넘어 유지 — 재시도가 중복 실행을 안 만든다."""
        s1 = JobStore(reap=False)
        job, existed = s1.create("crid-영속-1")
        assert existed is False
        s1.run(job, lambda: {"v": 1})

        s2 = JobStore(reap=False)          # 재시작
        same, existed2 = s2.create("crid-영속-1")
        assert existed2 is True             # 기존 job 반환
        assert same.job_id == job.job_id
        assert same.result == {"v": 1}


class TestZombieReaping:
    def test_running_job_reaped_on_restart(self):
        """재시작 시 running 고착은 error로 수확 — 스레드가 죽었으니 되살아나지 않는다.
        (running 고착은 A2A SSE 스트림 무한 루프의 원인)"""
        s1 = JobStore(reap=False)
        job, _ = s1.create()
        job.status = JobStatus.running      # 실행 중 서버가 죽은 상황
        s1._put(job)

        s2 = JobStore(reap=True)            # 재시작 — 좀비 수확
        restored = s2.get(job.job_id)
        assert restored.status == JobStatus.error
        assert "재시작" in restored.error["message"]

    def test_queued_job_also_reaped(self):
        s1 = JobStore(reap=False)
        job, _ = s1.create()                # queued 상태로 영속화됨
        JobStore(reap=True)                 # 재시작 수확
        assert JobStore(reap=False).get(job.job_id).status == JobStatus.error

    def test_done_job_not_reaped(self):
        s1 = JobStore(reap=False)
        job, _ = s1.create()
        s1.run(job, lambda: {"ok": True})
        JobStore(reap=True)                 # 수확이 완료 job을 건드리면 안 됨
        assert JobStore(reap=False).get(job.job_id).status == JobStatus.done


class TestA2ATaskMetaDurability:
    def test_task_meta_survives_cache_loss(self):
        """A2A 메타(skill·contextId·history)가 캐시 소실 후에도 DB에서 복원 —
        재시작 후 tasks/get이 완전한 Task를 준다(예전엔 메타 없는 반쪽)."""
        job_id = "job-meta-1"
        meta = {"skill": "represent", "contextId": "ctx-1",
                "history": [{"role": "user", "kind": "message"}]}
        a2a_mod._meta_put(job_id, meta)
        a2a_mod._task_meta.clear()          # 재시작 — 캐시 비움
        a2a_mod._canceled.clear()

        got, canceled = a2a_mod._meta_get(job_id)
        assert got["skill"] == "represent"
        assert got["contextId"] == "ctx-1"
        assert canceled is False

    def test_cancel_flag_survives_cache_loss(self):
        job_id = "job-meta-2"
        meta = {"skill": "judge", "contextId": "ctx-2", "history": []}
        a2a_mod._meta_put(job_id, meta, canceled=True)
        a2a_mod._task_meta.clear()
        a2a_mod._canceled.clear()

        _, canceled = a2a_mod._meta_get(job_id)
        assert canceled is True             # 취소 마킹도 재시작 생존

    def test_missing_meta_returns_empty(self):
        a2a_mod._task_meta.clear()
        meta, canceled = a2a_mod._meta_get("없는job")
        assert meta == {} and canceled is False


class TestA2ATaskGetAfterRestart:
    def test_tasks_get_returns_completed_task_after_restart(self):
        """엔드투엔드 — A2A tasks/get이 재시작 후에도 완료 Task를 돌려준다.
        (영속화 전엔 -32001 Task 없음이 나던 프로토콜 구멍)"""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)

        body = {"jsonrpc": "2.0", "id": "s", "method": "message/send",
                "params": {"message": {"role": "user", "kind": "message",
                    "messageId": "m", "parts": [{"kind": "data", "data": {
                        "skill": "represent",
                        "input": {"assets": [{"type": "text", "content":
                            "이름: 다이브인그룹\n국가: 한국\n산업: h\n설명: d\n문제: p\n"
                            "솔루션: s\n타겟: t\n판매가치: 매출"}]}}}]}}}
        task_id = client.post("/a2a", json=body).json()["result"]["id"]
        # 완료 대기
        for _ in range(60):
            got = client.post("/a2a", json={"jsonrpc": "2.0", "id": "g",
                                            "method": "tasks/get",
                                            "params": {"id": task_id}}).json()
            if got["result"]["status"]["state"] in ("completed", "failed",
                                                    "input-required"):
                break

        # 재시작 시뮬레이션 — job 메모리 캐시 + A2A 메타 캐시 비움
        from app.jobs import store as job_store
        job_store._jobs.clear()
        a2a_mod._task_meta.clear()
        a2a_mod._canceled.clear()

        after = client.post("/a2a", json={"jsonrpc": "2.0", "id": "g2",
                                          "method": "tasks/get",
                                          "params": {"id": task_id}}).json()
        assert "error" not in after                      # 예전엔 -32001
        assert after["result"]["id"] == task_id
        assert after["result"]["metadata"]["skill"] == "represent"   # 메타 복원
