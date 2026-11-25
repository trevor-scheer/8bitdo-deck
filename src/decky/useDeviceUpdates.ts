import { useCallback, useEffect, useRef, useState } from "react";
import { Device, Job, Model, Release, getJob, getModels, getReleases, getUpdateQueue, installUpdates, prepareUpdate, scanDevices } from "../api";
import { automaticDevice, chooseLatest, compareVersions, deviceKey } from "./firmware";

export interface DeviceUpdate {
  device: Device;
  release?: Release;
  job?: Job;
  status: string;
  error?: string;
}

type CachedJob = { key: string; release: string; id: string };
const CACHE_KEY = "8bitdo-deck:prepared-devices";
const LAST_JOB = "8bitdo-deck:last-job";
const DISMISSED_JOB = "8bitdo-deck:dismissed-job";
export const activeJob = (job?: Job | null): boolean => !!job && (["queued", "downloading", "validating", "installing", "verifying"].includes(job.state) || !!job.jobs?.some(activeJob));
export const messageOf = (error: unknown) => error instanceof Error ? error.message : String(error);

function readCache(): CachedJob[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(CACHE_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((item) => item && typeof item.key === "string" && typeof item.release === "string" && typeof item.id === "string").slice(0, 64) : [];
  } catch { return []; }
}

function save(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* An open panel can still track jobs without browser storage. */ }
}

export function useDeviceUpdates() {
  const alive = useRef(false);
  const generation = useRef(0);
  const checkingRef = useRef(false);
  const writingRef = useRef(true);
  const cache = useRef(readCache());
  const [rows, setRows] = useState<DeviceUpdate[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [checking, setChecking] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const [starting, setStarting] = useState(false);
  const [progress, setProgress] = useState<Job | null>(null);
  const [progressError, setProgressError] = useState("");
  const [error, setError] = useState("");
  const [pollRevision, setPollRevision] = useState(0);
  const finished = useRef("");

  const refresh = useCallback(async (retry = false) => {
    if (checkingRef.current || writingRef.current) return;
    checkingRef.current = true;
    if (retry) { cache.current = []; save(CACHE_KEY, "[]"); }
    setChecking(true);
    setError("");
    const request = ++generation.current;
    const current = () => alive.current && generation.current === request;
    const update = (id: string, values: Partial<DeviceUpdate>) => {
      if (current()) setRows((old) => old.map((row) => row.device.id === id ? { ...row, ...values } : row));
    };
    try {
      // Discovery and preparation probe shared receiver interfaces. Keep them sequential.
      const result = await scanDevices();
      const availableModels = await getModels();
      if (!current()) return;
      setModels(availableModels);
      setWarnings(result.warnings);
      setRows(result.devices.map((device) => ({ device, status: automaticDevice(device, availableModels) ? "Checking firmware…" : "Identification needed" })));
      const releaseLists = new Map<string, Promise<{ releases: Release[]; error?: string }>>();
      cache.current = cache.current.filter((entry) => result.devices.some((device) => deviceKey(device) === entry.key));
      save(CACHE_KEY, JSON.stringify(cache.current));
      for (const device of result.devices) {
        if (!current()) return;
        if (!automaticDevice(device, availableModels) || !device.model_id) continue;
        try {
          const modelId = device.model_id;
          if (!releaseLists.has(modelId)) releaseLists.set(modelId, getReleases(modelId));
          const listed = await releaseLists.get(modelId)!;
          if (!current()) return;
          if (listed.error) throw new Error(listed.error);
          const release = chooseLatest(listed.releases, modelId);
          if (!release) {
            update(device.id, { status: "No automatic update", error: "No unambiguous stable firmware version is available. See manual firmware browsing." });
            continue;
          }
          update(device.id, { release });
          const comparison = device.firmware_version ? compareVersions(release.version, device.firmware_version, modelId) : undefined;
          if (comparison !== undefined && comparison <= 0) {
            update(device.id, { status: comparison === 0 ? "Up to date" : "Newer firmware installed" });
            continue;
          }
          if (!release.downloadable) {
            update(device.id, { status: "Update unavailable", error: "The newest firmware cannot be downloaded from this source." });
            continue;
          }
          update(device.id, { status: "Downloading…" });
          const key = deviceKey(device);
          const cached = cache.current.find((entry) => entry.key === key && entry.release === release.id);
          let job: Job | undefined;
          if (cached) {
            try { job = await getJob(cached.id); }
            catch { /* Missing or expired preparation is recreated, never installed automatically. */ }
          }
          if (!current()) return;
          if (!job) {
            const prepared = await prepareUpdate(device.id, modelId, release.id);
            if (!prepared.job_id || prepared.error) throw new Error(prepared.error ?? "Could not prepare firmware.");
            cache.current = [...cache.current.filter((entry) => entry.key !== key), { key, release: release.id, id: prepared.job_id }];
            save(CACHE_KEY, JSON.stringify(cache.current));
            job = await getJob(prepared.job_id);
          }
          while (activeJob(job)) {
            update(device.id, { job, status: job.state === "downloading" ? "Downloading…" : "Checking firmware…" });
            await new Promise((resolve) => setTimeout(resolve, 1000));
            if (!current()) return;
            job = await getJob(job.id);
          }
          update(device.id, { job, status: job.can_install && job.state === "ready" ? "Ready to install" : "Install unavailable" });
        } catch (reason) {
          update(device.id, { status: "Check failed", error: messageOf(reason) });
        }
      }
    } catch (reason) {
      if (current()) setError(`Could not check devices: ${messageOf(reason)}`);
    } finally {
      if (current()) {
        checkingRef.current = false;
        setChecking(false);
      }
    }
  }, []);

  const applyProgress = useCallback((job: Job) => {
    setProgress(job);
    const children = job.jobs ?? [job];
    setRows((old) => old.map((row) => {
      const child = children.find((item) => item.id === row.job?.id);
      if (!child) return row;
      const status = child.state === "complete" ? "Updated" : child.state === "failed" ? "Update failed"
        : child.state === "cancelled" ? "Not updated" : child.state === "blocked" ? "Needs attention" : "Updating…";
      return { ...row, job: child, status };
    }));
  }, []);

  const watch = useCallback((job: Job) => {
    save(LAST_JOB, job.id);
    writingRef.current = activeJob(job);
    setProgressError("");
    applyProgress(job);
    setPollRevision((value) => value + 1);
  }, [applyProgress]);

  useEffect(() => {
    alive.current = true;
    void (async () => {
      try {
        let job = await getUpdateQueue();
        let saved = "";
        let dismissed = "";
        try {
          saved = localStorage.getItem(LAST_JOB) ?? "";
          dismissed = localStorage.getItem(DISMISSED_JOB) ?? "";
        } catch { /* Backend queue status remains available without browser storage. */ }
        if (!activeJob(job) && saved && saved !== job?.id) {
          try { job = await getJob(saved); }
          catch (reason) {
            if (!/not found/i.test(messageOf(reason))) throw reason;
            save(LAST_JOB, "");
          }
        }
        if (job?.id === dismissed && !activeJob(job)) job = null;
        if (!alive.current) return;
        if (job) watch(job);
        else writingRef.current = false;
        if (!activeJob(job)) void refresh();
      } catch (reason) {
        if (alive.current) setProgressError(`Could not restore update status: ${messageOf(reason)}. Reopen the plugin to retry.`);
      } finally {
        if (alive.current) setRestoring(false);
      }
    })();
    return () => { alive.current = false; generation.current++; checkingRef.current = false; };
  }, [refresh, watch]);

  useEffect(() => {
    if (!progress?.id) return;
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const job = await getJob(progress.id);
        if (cancelled) return;
        applyProgress(job);
        setProgressError("");
        writingRef.current = activeJob(job);
        if (activeJob(job)) timeout = setTimeout(() => { void poll(); }, 1000);
        else if (["complete", "completed"].includes(job.state) && finished.current !== job.id) {
          finished.current = job.id;
          cache.current = [];
          save(CACHE_KEY, "[]");
          void refresh();
        }
      } catch (reason) {
        if (!cancelled) {
          // A lost response does not prove the worker stopped. Keep writes disabled.
          writingRef.current = true;
          setProgressError(`Update status unavailable: ${messageOf(reason)}. Retrying…`);
          timeout = setTimeout(() => { void poll(); }, 3000);
        }
      }
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timeout); };
  }, [progress?.id, pollRevision, refresh, applyProgress]);

  const install = async (selected: DeviceUpdate[]) => {
    if (checkingRef.current || writingRef.current || !selected.length) return;
    const ids = selected.filter((row) => row.job?.state === "ready" && row.job.can_install).map((row) => row.job!.id);
    if (ids.length !== selected.length) return;
    writingRef.current = true;
    setStarting(true);
    setError("");
    try {
      const job = await installUpdates(ids, true);
      if (alive.current) watch(job);
    } catch (reason) {
      // The backend may have accepted the queue before the connection failed.
      try {
        const queued = await getUpdateQueue();
        if (!alive.current) return;
        if (queued && (activeJob(queued) || ids.every((id) => queued.jobs?.some((item) => item.id === id)))) watch(queued);
        else { writingRef.current = false; setError(messageOf(reason)); }
      } catch {
        if (alive.current) setProgressError("Could not confirm whether the update started. Reopen the plugin to restore its status before trying again.");
      }
    } finally { if (alive.current) setStarting(false); }
  };

  const dismiss = () => {
    if (writingRef.current || checkingRef.current) return;
    if (progress) save(DISMISSED_JOB, progress.id);
    setProgress(null);
    save(LAST_JOB, "");
  };

  return {
    rows, models, warnings, checking, progress, progressError, error, refresh, install, watch, dismiss,
    disabled: checking || restoring || starting || activeJob(progress) || !!progressError,
    ready: rows.filter((row) => row.job?.state === "ready" && row.job.can_install),
  };
}
