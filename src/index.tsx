import { definePlugin } from "@decky/api";
import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, staticClasses } from "@decky/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Device, Job, Model, RecoveryAccess, Release, checkRecovery, exportDiagnostics, getJob, getModels, getReleases,
  getRecoveryAccess, installUpdate, openOfficialUpdater, prepareUpdate, recoverUpdate, scanDevices, setupRecoveryAccess,
} from "./api";

const LAST_JOB = "8bitdo-deck:last-job";
const textStyle = { fontSize: 13, lineHeight: 1.5, overflowWrap: "anywhere" as const, margin: "8px 0" };

interface Selection {
  deviceId: string;
  modelId: string;
  identity: string;
  browsing: boolean;
}

const emptySelection: Selection = { deviceId: "", modelId: "", identity: "", browsing: false };

function deviceIdentity(device: Device): string {
  return JSON.stringify([device.id, device.vid, device.pid, device.role, device.model_id]);
}

function selectionForDevice(device: Device): Selection {
  return { deviceId: device.id, modelId: device.model_id ?? "", identity: deviceIdentity(device), browsing: false };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function savedJob(): string {
  try { return localStorage.getItem(LAST_JOB) ?? ""; } catch { return ""; }
}

function saveJob(id: string) {
  try {
    if (id) localStorage.setItem(LAST_JOB, id);
    else localStorage.removeItem(LAST_JOB);
  } catch { /* The current job remains usable when browser storage is unavailable. */ }
}

function RecoveryAccessSettings({ disabled }: { disabled: boolean }) {
  const alive = useRef(true);
  const [access, setAccess] = useState<RecoveryAccess | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    alive.current = true;
    void getRecoveryAccess().then((result) => {
      if (alive.current) setAccess(result);
    }).catch((reason: unknown) => {
      if (alive.current) setError(`Could not check recovery access: ${errorMessage(reason)}`);
    });
    return () => { alive.current = false; };
  }, []);

  useEffect(() => {
    if (!access?.pending) return;
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await getRecoveryAccess();
        if (cancelled) return;
        setAccess(result);
        if (result.pending) timeout = setTimeout(() => { void poll(); }, 1000);
      } catch (reason) {
        if (!cancelled) {
          setAccess(null);
          setError(`Could not check recovery access: ${errorMessage(reason)}. Reopen the plugin to check again.`);
        }
      }
    };
    timeout = setTimeout(() => { void poll(); }, 1000);
    return () => { cancelled = true; clearTimeout(timeout); };
  }, [access?.pending]);

  return <>
    <p role="status" style={textStyle}>{access?.message ?? (error ? "" : "Checking receiver recovery access…")}</p>
    {!access?.configured && <PanelSectionRow>
      <ButtonItem
        layout="below"
        description="Opens SteamOS administrator authorization. Does not write firmware."
        disabled={disabled || starting || !access?.can_setup || access.pending}
        onClick={() => {
          setStarting(true);
          setError("");
          void setupRecoveryAccess().then((result) => {
            if (alive.current) setAccess(result);
          }).catch((reason: unknown) => {
            if (alive.current) setError(`Could not set up recovery access: ${errorMessage(reason)}`);
          }).finally(() => { if (alive.current) setStarting(false); });
        }}
      >Set up receiver recovery</ButtonItem>
    </PanelSectionRow>}
    {error && <p role="alert" style={textStyle}>{error}</p>}
  </>;
}

function Content() {
  const alive = useRef(true);
  const selectionRef = useRef<Selection>(emptySelection);
  const selectionRevision = useRef(0);
  const scanRevision = useRef(0);
  const [devices, setDevices] = useState<Device[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [modelId, setModelId] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const [releases, setReleases] = useState<Release[]>([]);
  const [releaseId, setReleaseId] = useState("");
  const [jobId, setJobId] = useState(savedJob);
  const [job, setJob] = useState<Job | null>(null);
  const [scanning, setScanning] = useState(false);
  const [loadingReleases, setLoadingReleases] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmInstall, setConfirmInstall] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const model = models.find((item) => item.id === modelId);
  const device = devices.find((item) => item.id === deviceId);
  const release = releases.find((item) => item.id === releaseId);
  const jobActive = !!job && ["queued", "downloading", "validating", "installing", "verifying"].includes(job.state);

  const applySelection = useCallback((selection: Selection) => {
    if (selection.modelId !== selectionRef.current.modelId) {
      setReleases([]);
      setReleaseId("");
    }
    selectionRef.current = selection;
    setDeviceId(selection.deviceId);
    setModelId(selection.modelId);
    setConfirmInstall(false);
  }, []);

  const scan = useCallback(async () => {
    const requestRevision = ++scanRevision.current;
    const previousSelectionRevision = selectionRevision.current;
    setScanning(true);
    setError("");
    try {
      const [result, availableModels] = await Promise.all([scanDevices(), getModels()]);
      if (!alive.current || requestRevision !== scanRevision.current) return;
      setDevices(result.devices);
      setWarnings(result.warnings);
      setModels(availableModels);
      const authoritative = (item: Device) => ["identified", "supported"].includes(item.status)
        && availableModels.some((candidate) => candidate.id === item.model_id);
      const current = selectionRef.current;
      const selectionChanged = previousSelectionRevision !== selectionRevision.current;
      if (current.deviceId) {
        const connected = result.devices.find((item) => item.id === current.deviceId);
        if (connected && authoritative(connected) && (!selectionChanged || deviceIdentity(connected) === current.identity)) {
          applySelection(selectionForDevice(connected));
        } else if (!connected || !selectionChanged || deviceIdentity(connected) !== current.identity) {
          applySelection(emptySelection);
        }
      } else if (!current.browsing && !selectionChanged) {
        const detected = result.devices.find(authoritative);
        applySelection(detected ? selectionForDevice(detected) : emptySelection);
      }
    } catch (reason) {
      if (alive.current && requestRevision === scanRevision.current) setError(`Could not scan devices: ${errorMessage(reason)}`);
    } finally {
      if (alive.current && requestRevision === scanRevision.current) setScanning(false);
    }
  }, [applySelection]);

  useEffect(() => {
    alive.current = true;
    void scan();
    return () => { alive.current = false; scanRevision.current++; };
  }, [scan]);

  useEffect(() => {
    let cancelled = false;
    setReleases([]);
    setReleaseId("");
    setConfirmInstall(false);
    if (!modelId) {
      setLoadingReleases(false);
      return () => { cancelled = true; };
    }
    setLoadingReleases(true);
    void getReleases(modelId).then((result) => {
      if (cancelled) return;
      if (result.error) throw new Error(result.error);
      setReleases(result.releases);
      setReleaseId(result.releases[0]?.id ?? "");
    }).catch((reason: unknown) => {
      if (!cancelled) setError(`Could not load firmware: ${errorMessage(reason)}`);
    }).finally(() => {
      if (!cancelled) setLoadingReleases(false);
    });
    return () => { cancelled = true; };
  }, [modelId]);

  useEffect(() => {
    if (!jobId || busy) return;
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await getJob(jobId);
        if (cancelled) return;
        setJob(result);
        if (!["complete", "completed", "failed", "cancelled", "ready", "blocked"].includes(result.state)) {
          timeout = setTimeout(() => { void poll(); }, 1000);
        }
      } catch (reason) {
        if (!cancelled) {
          const message = `Could not read update progress: ${errorMessage(reason)}`;
          setError(message);
          setJob({ id: jobId, state: "failed", progress: 0, can_install: false, message });
        }
      }
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timeout); };
  }, [jobId, busy]);

  const act = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try { await action(); }
    catch (reason) { if (alive.current) setError(errorMessage(reason)); }
    finally { if (alive.current) setBusy(false); }
  };

  const selectDevice = (selected: Device) => {
    selectionRevision.current++;
    applySelection(selectionForDevice(selected));
    setError("");
  };

  return <>
    <PanelSection title="Devices">
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={scanning || busy || jobActive} onClick={() => void scan()}>
          {scanning ? "Scanning…" : "Scan connected devices"}
        </ButtonItem>
      </PanelSectionRow>
      {!scanning && !devices.length && <p style={textStyle}>Connect your controller or receiver by USB, then scan. You can also browse firmware by model below.</p>}
      {devices.map((item) => <PanelSectionRow key={item.id}>
        <ButtonItem
          layout="below"
          disabled={busy || jobActive}
          description={`${item.role} · ${item.firmware_version ? `Firmware ${item.firmware_version}` : "Firmware version unknown"}${item.id === deviceId ? " · Selected" : ""}`}
          onClick={() => selectDevice(item)}
        >{item.name}</ButtonItem>
      </PanelSectionRow>)}
      {device && <p style={textStyle}>{device.reason || device.status}</p>}
      {warnings.map((warning, index) => <p key={index} style={textStyle}>{warning}</p>)}
    </PanelSection>

    <PanelSection title="Firmware">
      <PanelSectionRow>
        <DropdownItem
          label="Model and component"
          strDefaultLabel="Select a model"
          description="Controller and receiver firmware are separate."
          rgOptions={models.map((item) => ({ data: item.id, label: `${item.name} (${item.role})` }))}
          selectedOption={modelId}
          disabled={busy || jobActive || !models.length}
          onChange={(option) => {
            selectionRevision.current++;
            applySelection(device && device.model_id === option.data
              ? selectionForDevice(device)
              : { deviceId: "", modelId: option.data, identity: "", browsing: true });
            setError("");
          }}
        />
      </PanelSectionRow>
      {model && <>
        <p style={textStyle}>{model.instructions}</p>
        <p style={textStyle}>{model.support}</p>
        {!device && <p style={textStyle}>Browsing firmware only. Selecting a model does not confirm that it matches connected hardware.</p>}
        {loadingReleases && <p style={textStyle}>Checking official firmware releases…</p>}
        {!loadingReleases && releases.length === 0 && <p style={textStyle}>No firmware releases are available from this source.</p>}
        {releases.length > 0 && <PanelSectionRow>
          <DropdownItem
            label="Firmware release"
            rgOptions={releases.map((item) => ({ data: item.id, label: `${item.version}${item.date ? ` · ${item.date}` : ""}` }))}
            selectedOption={releaseId}
            disabled={busy || jobActive || loadingReleases}
            onChange={(option) => { setReleaseId(option.data); setConfirmInstall(false); }}
          />
        </PanelSectionRow>}
        {release?.notes && <p style={{ ...textStyle, whiteSpace: "pre-wrap" }}>{release.notes}</p>}
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            description="Checks the selected update without writing firmware."
            disabled={busy || jobActive || loadingReleases || !release?.downloadable}
            onClick={() => void act(async () => {
              const targetId = device?.model_id === modelId && ["identified", "supported"].includes(device.status) ? device.id : "";
              const result = await prepareUpdate(targetId, modelId, releaseId);
              if (result.error || !result.job_id) throw new Error(result.error ?? "The download could not be started.");
              if (!alive.current) return;
              setJob(null);
              setJobId(result.job_id);
              saveJob(result.job_id);
              setConfirmInstall(false);
            })}
          >Prepare firmware</ButtonItem>
        </PanelSectionRow>
      </>}
    </PanelSection>

    {job && <PanelSection title="Update progress">
      {job.target_label && <p style={{ ...textStyle, fontWeight: "bold" }}>{job.target_label}</p>}
      <p style={textStyle}>{job.message}</p>
      <progress aria-label="Update progress" value={Math.max(0, Math.min(100, job.progress))} max={100} style={{ width: "100%" }} />
      {job.evidence_level === "source-reviewed-experimental" && <p style={textStyle}>Experimental update: the protocol has been reviewed, but a complete update has not yet been validated on hardware.</p>}
      {job.can_install && !confirmInstall && <PanelSectionRow>
        <ButtonItem disabled={busy || jobActive} layout="below" onClick={() => setConfirmInstall(true)}>Review installation</ButtonItem>
      </PanelSectionRow>}
      {job.can_install && confirmInstall && <>
        <p style={textStyle}>Install {job.target_label || "the prepared firmware on the verified device"}? Keep USB connected and keep the Deck awake until verification finishes.</p>
        {job.evidence?.trim() && <p style={{ ...textStyle, whiteSpace: "pre-wrap" }}>{job.evidence}</p>}
        <PanelSectionRow>
          <ButtonItem disabled={busy || jobActive} layout="below" onClick={() => void act(async () => {
            setConfirmInstall(false);
            const result = await installUpdate(job.id);
            if (alive.current) setJob(result);
          })}>Confirm firmware installation</ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow><ButtonItem disabled={busy} layout="below" onClick={() => setConfirmInstall(false)}>Go back</ButtonItem></PanelSectionRow>
      </>}
      {["failed", "blocked"].includes(job.state) && job.can_check_recovery && !job.recovery_ready && <PanelSectionRow>
        <ButtonItem disabled={busy || jobActive} layout="below" description="Checks this interrupted update without writing firmware." onClick={() => void act(async () => {
          const result = await checkRecovery(job.id);
          if (alive.current) setJob(result);
        })}>Check interrupted update</ButtonItem>
      </PanelSectionRow>}
      {job.recovery_ready && <>
        <p style={textStyle}>{job.recovery_action === "reflash"
          ? "Recovery will erase the receiver firmware and reinstall the exact cached image for this interrupted update. Keep USB connected and keep the Deck awake."
          : "The receiver state has been checked. Recovery may finish an already committed update. Keep USB connected."}</p>
        <PanelSectionRow>
          <ButtonItem disabled={busy || jobActive} layout="below" onClick={() => void act(async () => {
            const result = await recoverUpdate(job.id, true);
            if (alive.current) setJob(result);
          })}>{job.recovery_action === "reflash" ? "Confirm erase and reinstall" : "Confirm receiver recovery"}</ButtonItem>
        </PanelSectionRow>
      </>}
      {!jobActive && <PanelSectionRow>
        <ButtonItem disabled={busy} layout="below" onClick={() => {
          setJob(null); setJobId(""); saveJob(""); setConfirmInstall(false);
        }}>Dismiss status</ButtonItem>
      </PanelSectionRow>}
    </PanelSection>}

    <PanelSection title="Other update options">
      <RecoveryAccessSettings disabled={busy || jobActive} />
      <p style={textStyle}>8BitDo’s web updater supports some models, but currently has no update path for the Ultimate Bluetooth receiver. USB access may need setup in Desktop Mode.</p>
      <PanelSectionRow>
        <ButtonItem disabled={busy || jobActive} layout="below" onClick={() => void act(async () => {
          const result = await openOfficialUpdater();
          if (!result.opened) throw new Error(result.error ?? "Could not open the official updater.");
          if (alive.current) setNotice("Opened the official updater in your browser.");
        })}>Open official updater</ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem disabled={busy || jobActive} layout="below" onClick={() => void act(async () => {
          const result = await exportDiagnostics();
          if (result.error || !result.path) throw new Error(result.error ?? "Could not export diagnostics.");
          if (alive.current) setNotice(`Diagnostics saved to ${result.path}`);
        })}>Export diagnostics</ButtonItem>
      </PanelSectionRow>
      {notice && <p role="status" style={textStyle}>{notice}</p>}
      {error && <p role="alert" style={textStyle}>{error}</p>}
    </PanelSection>
  </>;
}

export default definePlugin(() => ({
  name: "8BitDo Deck",
  titleView: <div className={staticClasses.Title}>8BitDo Deck</div>,
  content: <Content />,
  icon: <svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
    <path d="M7 6h10c2 0 3 2 3.5 4l1 7c.3 2-1.7 3-3 1l-2-3h-9l-2 3c-1.3 2-3.3 1-3-1l1-7C4 8 5 6 7 6Z" />
    <path d="M6 10h5M8.5 7.5v5" />
    <circle cx="16" cy="9" r=".8" fill="currentColor" stroke="none" />
    <circle cx="18" cy="12" r=".8" fill="currentColor" stroke="none" />
  </svg>,
}));
