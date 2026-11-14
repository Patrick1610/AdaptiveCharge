"""Finite owner-authorized overnight probe via an existing foreground HA client.

Only public AdaptiveCharge inputs and one HA restart are allowed. This is not a
charger controller: it never writes an EVSE mode, switch, amp, phase or car limit.
Run once with a concrete, reviewed settings receipt and an exclusive output dir.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from math import floor, isfinite
from pathlib import Path
import time

DOMAIN = "adaptive_charge_preview"


def own(session: dict, binding: dict | None) -> bool:
    """Own the exact nonterminal request, not merely a vehicle or generation."""
    return bool(binding and not session.get("terminal") and all(
        session.get(k) == binding.get(k)
        for k in ("vehicle_id", "generation", "started_at", "origin_meter")
    ))


def snapshot(h, settings: dict) -> dict:
    """Read public states; preserve source timestamps and selected full attrs."""
    states = {s["entity_id"]: s for s in h.request("states")}
    root = states.get(settings["root_entity"], {})
    row = states.get(settings["vehicle_entity"], {}).get("attributes", {})
    if "control_enabled" not in root.get("attributes", {}) or "session" not in row:
        raise LookupError("Integration entities not readable yet")
    selected = set(settings["evidence_entities"]) | {
        settings["root_entity"], settings["vehicle_entity"], settings["planning_entity"]}
    return {"utc": datetime.now(timezone.utc).isoformat(), "root": root.get("attributes", {}),
            "row": row, "states": {e: states.get(e) for e in sorted(selected)}}


def physical(record: dict) -> bool:
    """Power readback; provenance and actual meter progression remain separate."""
    p = record["root"].get("physical", {})
    return bool(p.get("fresh") and isinstance(p.get("kw"), (int, float)) and p["kw"] >= .2)


def same_target(session: dict, expected: dict) -> bool:
    """An absent/cleared user input is a change, never an arithmetic failure."""
    return all(isinstance(session.get(k), (int,float)) and abs(session[k]-v) <= .05 for k,v in expected.items())


def contract(record: dict, descriptions: list, settings: dict) -> None:
    """Validate real public entity and action contracts before the first Start."""
    root, row = record["root"], record["row"]
    required = {"integration_version","control_enabled","command_generation","active_vehicle",
                "physical","meter_kwh","control_pending","owned_manual","limit_cleanup_pending"}
    if not required <= root.keys() or not {"phase","session","demand","observations","maximum_soc"} <= row.keys():
        raise ValueError("Incomplete real integration attribute contract")
    if (type(root["control_enabled"]) is not bool or type(root["command_generation"]) is not int
            or not isinstance(root["physical"],dict) or not isinstance(row["demand"],dict)
            or not isinstance(row["observations"],dict) or not isinstance(row["phase"],str)):
        raise ValueError("Invalid real integration attribute types")
    if not isinstance(row["maximum_soc"],(int,float)) or not 80 <= row["maximum_soc"] <= settings["authorised_maximum_soc"] <= 90:
        raise ValueError("Configured vehicle limit exceeds the authorized band")
    service = next((d.get("services",{}) for d in descriptions if d.get("domain")==DOMAIN),{})
    for action in ("start","set_target","stop"):
        fields = service.get(action,{}).get("fields",{})
        expected = {"vehicle_id","entry_id"} | ({"target_range_km","departure"} if action!="stop" else set())
        if not expected <= fields.keys():
            raise ValueError("Registered integration action/schema mismatch")


def run(h, settings: dict, directory: str | Path, *, clock=time.time, sleep=time.sleep) -> dict:
    """Execute one finite probe, journal intents before calls and never retry them.

    A restart/read failure does not create a new Start. User Stop, Master-off,
    different target/departure or another generation immediately relinquishes the
    probe. Stop at the finite end only while the exact own request still exists.
    """
    start, end = clock(), settings["end_utc"]
    if not start < end <= start + 9 * 3600:
        raise ValueError("Concrete end must be within nine hours")
    if settings["version"] != "5.2.0b1" or not 80 <= settings["final_soc"] <= settings["authorised_maximum_soc"] <= 90:
        raise ValueError("Unexpected reviewed version or charge bound")
    post_stop_seconds = settings["post_stop_seconds"]
    if not isinstance(post_stop_seconds,int) or not 0 <= post_stop_seconds <= 600:
        raise ValueError("Post-stop read-only allowance must be explicitly bounded")
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    receipt_path = output / "receipt.json"
    with receipt_path.open("x") as f:
        json.dump({"phase": "created", "settings": settings}, f)
    receipt = {"phase": "preflight", "settings": settings, "started_utc": start,
               "actions": [], "observations": [], "binding": None}

    def save():
        temp = output / "receipt.pending.json"
        temp.write_text(json.dumps(receipt, indent=2) + chr(10))
        temp.replace(receipt_path)

    def log(record):
        with (output / "samples.jsonl").open("a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + chr(10))

    expected = {}
    latest = None

    def call(action, data=None):
        item = {"utc": clock(), "action": action, "data": data or {}, "phase": "intent"}
        receipt["actions"].append(item)
        save()  # Persist uncertainty before transmission: no automatic retry.
        print("NIGHT_PROBE_ACTION",action,item["utc"],flush=True)
        if action == "restart":
            response = h.request("services/homeassistant/restart", {}, "POST")
        else:
            response = h.request("services/" + DOMAIN + "/" + action,
                                 {"vehicle_id": settings["vehicle_id"], "entry_id": settings["entry_id"], **(data or {})}, "POST")
        item["phase"] = "returned"
        save()
        return response

    def target(soc, departure):
        data = {"target_range_km": floor(full_range * soc / 100 * 100) / 100,
                "departure": datetime.fromtimestamp(departure, timezone.utc).isoformat()}
        expected.update(target_km=data["target_range_km"], departure=departure)
        return data

    try:
        latest = snapshot(h, settings)
        log(latest)
        contract(latest,h.request("services"),settings)
        root, row = latest["root"], latest["row"]
        if root.get("integration_version") != settings["version"] or not root.get("control_enabled"):
            raise ValueError("Reviewed version/Master not loaded")
        if root.get("active_vehicle") or row.get("session") and not row["session"].get("terminal"):
            raise ValueError("Existing request must not be adopted")
        if row.get("observations", {}).get("connected") is not True:
            raise ValueError("Tesla not reported connected")
        full_range = row.get("demand", {}).get("full_range_km")
        soc = row.get("observations", {}).get("soc", {}).get("value")
        if not isinstance(full_range, (int, float)) or not isfinite(full_range) or full_range <= 0:
            raise ValueError("No bounded range reference")
        if not isinstance(soc, (int, float)) or not 0 < soc < 80:
            raise ValueError("Tesla SOC outside this probe's concrete preflight")
        previous_generation = root.get("command_generation")
        if not isinstance(previous_generation, int):
            raise ValueError("No command-generation binding")
        short_soc = min(soc + 4, 78)
        short_departure = start + 30 * 60
        call("start", target(short_soc, short_departure))
        receipt["phase"] = "waiting_start_readback"
        save()
        phase = "short"
        phase_since = clock()
        first_power = None
        restart_done = False
        restart_seen = False
        last_error = None
        settling_until = clock() + 90
        last_progress = 0
        while clock() < end:
            try:
                latest = snapshot(h, settings)
                last_error = None
            except Exception as exc:
                # A service/restart read failure never authorizes a second action.
                last_error = type(exc).__name__
                receipt["last_read_error"] = last_error
                save()
                if restart_done and clock()-receipt["restart_intent_utc"] > 600:
                    raise RuntimeError("Integration still unreadable after bounded restart grace") from exc
                sleep(min(30, max(0, end-clock())))
                continue
            log(latest)
            root, row = latest["root"], latest["row"]
            session = row.get("session") or {}
            if receipt["binding"] is None:
                if not session.get("terminal") and all(session.get(k) is not None for k in ("generation", "started_at", "origin_meter")):
                    if (session.get("started_at", 0) < start-2 or session.get("target_km") != expected["target_km"]
                            or session.get("generation") != previous_generation+1 or session.get("vehicle_id") != settings["vehicle_id"]):
                        raise ValueError("Start readback is not the requested command")
                    receipt["binding"] = {k: session.get(k) for k in ("vehicle_id", "generation", "started_at", "origin_meter")}
                    receipt["phase"] = "native_running"
                    save()
                elif clock()-phase_since > 90:
                    raise ValueError("No unambiguous Start readback; no retry")
                else:
                    sleep(10)
                    continue
            if not own(session, receipt["binding"]) or not root.get("control_enabled"):
                receipt["phase"] = "relinquished_stop_disconnect_or_foreign_command"
                save()
                return receipt
            requested_soc = row.get("demand",{}).get("requested_soc")
            if isinstance(requested_soc,(int,float)) and requested_soc > settings["authorised_maximum_soc"]:
                raise ValueError("Derived target exceeds the authorized SOC band")
            if clock() > settling_until and not same_target(session,expected):
                receipt["phase"] = "relinquished_changed_target_or_departure"
                save()
                return receipt
            if physical(latest):
                first_power = first_power or clock()
            if clock()-last_progress >= 300:
                print("NIGHT_PROBE_STATE",clock(),phase,row.get("phase"),root.get("physical"),root.get("control_fault"),flush=True)
                last_progress=clock()
            if first_power and not restart_done and clock()-first_power >= 180:
                restart_done = True  # Mark first, including an ambiguous HTTP return.
                receipt["restart_intent_utc"] = clock()
                save()
                try:
                    call("restart")
                except Exception as exc:
                    receipt["restart_return_uncertain"] = type(exc).__name__
                    save()
                sleep(45)
                continue
            if restart_done and not restart_seen and physical(latest):
                changed = (latest["states"].get(settings["root_entity"]) or {}).get("last_changed")
                if changed and datetime.fromisoformat(changed).timestamp() >= receipt["restart_intent_utc"]:
                    restart_seen = True
                    receipt["observations"].append({"kind": "same_request_after_restart_with_power_readback", "utc": clock(), "meter_kwh": root.get("meter_kwh"),
                        "power_provenance":root.get("physical"),"independent_physical_confirmation":root.get("physical",{}).get("independent") is True})
                    save()
            if phase == "short" and first_power and clock()-first_power >= 420:
                call("set_target", target(min(short_soc+2, 79), short_departure))
                phase, phase_since = "raised", clock()
                settling_until = clock()+60
            elif phase == "raised" and clock()-phase_since >= 120:
                call("set_target", target(max(soc+2, short_soc-1), short_departure))
                phase, phase_since = "lowered", clock()
                settling_until = clock()+60
            elif phase == "lowered" and (row.get("phase") in {"watching", "within_tolerance", "verifying"} or clock()-phase_since >= 1200):
                receipt["observations"].append({"kind":"short_goal_end", "utc":clock(), "phase":row.get("phase"), "reason":row.get("reason")})
                call("set_target", target(settings["final_soc"], end-15*60))
                phase, phase_since = "final_goal", clock()
                settling_until = clock()+60
            elif phase in {"short", "raised"} and clock()-start > 40*60:
                # If physical entry did not occur, preserve the useful ordinary goal.
                receipt["observations"].append({"kind":"short_sequence_not_physically_observed", "utc":clock()})
                call("set_target", target(settings["final_soc"], end-15*60))
                phase, phase_since = "final_goal", clock()
                settling_until = clock()+60
            reported_soc = row.get("observations", {}).get("soc", {}).get("value")
            if isinstance(reported_soc, (int, float)) and reported_soc > 90:
                receipt["observations"].append({"kind":"upper_soc_readback", "utc":clock()})
                break
            sleep(min(30, max(0, end-clock())))
        latest = snapshot(h, settings)  # Never act from an old pre-outage snapshot.
        log(latest)
        final_session = latest["row"].get("session") or {}
        unchanged = same_target(final_session,expected)
        if own(final_session, receipt["binding"]) and unchanged and latest["root"].get("control_enabled"):
            call("stop")
            receipt["phase"] = "finite_end_stop_requested"
            observation_end=end+post_stop_seconds
            while clock()<observation_end:
                sleep(min(30,observation_end-clock()))
                post = snapshot(h, settings)
                log(post)
                if not (post["row"].get("session") or {}).get("terminal"):
                    receipt["phase"] = "post_stop_foreign_request_preserved"
                    break
                if (post["root"].get("control_pending") is None and not post["root"].get("owned_manual")
                        and not post["root"].get("limit_cleanup_pending") and not post["root"].get("economic_pause")):
                    receipt["observations"].append({"kind":"stop_terminal_executor_and_limit_cleanup_complete", "utc":clock(),
                        "control_status":post["root"].get("control_status"),"power_provenance":post["root"].get("physical")})
                    receipt["phase"]="finished_requires_history_assessment"
                    break
            else:
                receipt["phase"] = "finite_end_cleanup_not_confirmed"
        else:
            receipt["phase"] = "finite_end_no_own_request"
    except Exception as exc:
        receipt["phase"] = "blocked_no_retry"
        receipt["error"] = type(exc).__name__ + ": " + str(exc)
        # No blind stop from stale data; integration owns its ordinary bounded job.
    receipt["ended_utc"] = clock()
    save()
    print(json.dumps({k:receipt.get(k) for k in ("phase","error","observations","ended_utc")}))
    return receipt
