import { definePlugin } from "@decky/api";
import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, staticClasses } from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import {
  Job, Model, RecoveryAccess, Release, checkRecovery, exportDiagnostics, getReleases,
  getRecoveryAccess, openOfficialUpdater, prepareUpdate, recoverUpdate, setupRecoveryAccess,
} from "./api";
import { DevicePicture } from "./shared/DevicePicture";
import { theme, progressValue } from "./shared/theme";
import { activeJob, messageOf as errorMessage, useDeviceUpdates } from "./decky/useDeviceUpdates";

const textStyle = { color: theme.text, fontSize: 13, lineHeight: 1.4, overflowWrap: "anywhere" as const, margin: "6px 0" };

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
    <p role="status" style={textStyle}>{access?.message ?? (error ? "" : "Checking USB recovery access…")}</p>
    {!access?.configured && <PanelSectionRow>
      <ButtonItem
        layout="below"
        description="Opens SteamOS administrator authorization. Does not write firmware."
        disabled={disabled || starting || !access?.can_setup || access.pending}
        onClick={() => {
          setStarting(true);
          setError("");
          void setupRecoveryAccess(true).then((result) => {
            if (alive.current) setAccess(result);
          }).catch((reason: unknown) => {
            if (alive.current) setError(`Could not set up recovery access: ${errorMessage(reason)}`);
          }).finally(() => { if (alive.current) setStarting(false); });
        }}
      >Set up USB recovery</ButtonItem>
    </PanelSectionRow>}
    {error && <p role="alert" style={textStyle}>{error}</p>}
  </>;
}

function ManualFirmware({ models, disabled, onJob }: { models: Model[]; disabled: boolean; onJob: (jobId: string) => void }) {
  const [modelId, setModelId] = useState("");
  const [releases, setReleases] = useState<Release[]>([]);
  const [releaseId, setReleaseId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const model = models.find((item) => item.id === modelId);
  const release = releases.find((item) => item.id === releaseId);
  useEffect(() => {
    let cancelled = false;
    setReleases([]);
    setReleaseId("");
    if (!modelId) return;
    setLoading(true);
    setError("");
    void getReleases(modelId).then((result) => {
      if (cancelled) return;
      if (result.error) throw new Error(result.error);
      setReleases(result.releases);
      setReleaseId(result.releases[0]?.id ?? "");
    }).catch((reason) => { if (!cancelled) setError(errorMessage(reason)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [modelId]);
  return <>
    <DropdownItem label="Browse firmware" strDefaultLabel="Select a model" selectedOption={modelId}
      disabled={disabled || loading} rgOptions={models.map((item) => ({ data: item.id, label: `${item.name} (${item.role})` }))}
      onChange={(option) => setModelId(option.data)} />
    {model && <>
      <p style={textStyle}>{model.instructions}</p>
      <p style={textStyle}>Manual downloads do not install firmware or identify connected hardware.</p>
      {!!releases.length && <DropdownItem label="Firmware release" selectedOption={releaseId}
        disabled={disabled || loading} rgOptions={releases.map((item) => ({ data: item.id, label: item.version }))}
        onChange={(option) => setReleaseId(option.data)} />}
      {release?.notes && <p style={{ ...textStyle, whiteSpace: "pre-wrap" }}>{release.notes}</p>}
      <ButtonItem layout="below" disabled={disabled || loading || !release?.downloadable} onClick={() => {
        setLoading(true);
        setError("");
        void prepareUpdate("", modelId, releaseId).then((result) => {
          if (result.error || !result.job_id) throw new Error(result.error ?? "Could not download firmware.");
          onJob(result.job_id);
        }).catch((reason) => setError(errorMessage(reason))).finally(() => setLoading(false));
      }}>Download firmware only</ButtonItem>
    </>}
    {error && <p role="alert" style={textStyle}>{error}</p>}
  </>;
}

function Content() {
  const updates = useDeviceUpdates();
  const [details, setDetails] = useState<string[]>([]);
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const disabled = updates.disabled || busy;
  const progress = updates.progress;
  const children = progress?.jobs ?? (progress ? [progress] : []);
  const act = async (action: () => Promise<void>) => {
    if (disabled) return;
    setBusy(true);
    setError("");
    try { await action(); }
    catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };
  const toggleDetails = (id: string) => setDetails((old) => old.includes(id) ? old.filter((key) => key !== id) : [...old, id]);
  const recover = async (job: Job, check: boolean) => {
    const result = check ? await checkRecovery(job.id) : await recoverUpdate(job.id, true);
    updates.watch(progress?.jobs ? { ...progress, jobs: progress.jobs.map((item) => item.id === job.id ? result : item) } : result);
  };

  return <>
    <PanelSection title="Firmware updates">
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={disabled || !updates.ready.length}
          description={updates.checking ? "Checking devices and downloading firmware…" : "Installs ready updates one at a time. Keep USB connected."}
          onClick={() => void updates.install(updates.ready)}>
          {`Update all${updates.ready.length ? ` (${updates.ready.length})` : ""}`}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow><ButtonItem layout="below" disabled={disabled} onClick={() => void updates.refresh(true)}>
        {updates.checking ? "Checking…" : "Check for updates"}
      </ButtonItem></PanelSectionRow>
      {updates.error && <p role="alert" style={textStyle}>{updates.error}</p>}
      {updates.progressError && <p role="alert" style={textStyle}>{updates.progressError}</p>}
      {progress && <>
        <p role="status" style={textStyle}>{progress.message}</p>
        <progress aria-label="Update progress" value={progressValue(progress.progress)} max={100} style={{ width: "100%", accentColor: theme.accent }} />
        {children.map((job) => <div key={job.id}>
          {progress.jobs && <p style={textStyle}>{job.target_label}: {job.state}</p>}
          {["failed", "blocked"].includes(job.state) && <p style={textStyle}>{job.message}</p>}
          {["failed", "blocked"].includes(job.state) && job.can_check_recovery && !job.recovery_ready && <ButtonItem
            layout="below" disabled={disabled} description="Checks the interrupted update without writing firmware."
            onClick={() => void act(() => recover(job, true))}>Check interrupted update</ButtonItem>}
          {job.recovery_ready && <ButtonItem layout="below" disabled={disabled}
            description={job.recovery_action === "reflash" ? "Erases firmware and reinstalls this update’s cached image. Keep USB connected." : "Finishes the interrupted update. Keep USB connected."}
            onClick={() => void act(() => recover(job, false))}>
            {job.recovery_action === "reflash" ? "Erase and reinstall" : "Recover device"}
          </ButtonItem>}
        </div>)}
        {!activeJob(progress) && <ButtonItem layout="below" disabled={disabled} onClick={updates.dismiss}>Dismiss status</ButtonItem>}
      </>}
    </PanelSection>

    <PanelSection title={`Devices${updates.rows.length ? ` (${updates.rows.length})` : ""}`}>
      {!updates.checking && !updates.rows.length && <p style={textStyle}>Connect a controller or receiver by USB. For wireless updates, turn on the controller in 2.4 GHz mode.</p>}
      {updates.rows.map((row) => {
        const { device, release, job } = row;
        const ready = job?.state === "ready" && job.can_install;
        const experimental = job?.evidence_level === "source-reviewed-experimental";
        const version = device.firmware_version ?? "Version unknown";
        const available = release && !["Up to date", "Newer firmware installed"].includes(row.status) ? ` → ${release.version}` : "";
        return <PanelSectionRow key={device.id}>
          <ButtonItem layout="inline" bottomSeparator="standard" disabled={ready ? disabled : false}
            icon={<DevicePicture modelId={device.model_id} name={device.name} size={40} />}
            label={<span style={{ fontSize: 12, overflowWrap: "anywhere" }}>{device.name}</span>}
            description={<span style={{ fontSize: 11 }}>{version}{available} · {row.status}{experimental ? " · Experimental" : ""}</span>}
            onClick={() => ready ? void updates.install([row]) : toggleDetails(device.id)}>
            {ready ? "Install" : details.includes(device.id) ? "Hide" : "Details"}
          </ButtonItem>
          {ready && advanced && <ButtonItem layout="below" onClick={() => toggleDetails(device.id)}>Firmware details</ButtonItem>}
          {details.includes(device.id) && <div style={{ padding: "0 8px 8px" }}>
            <p style={textStyle}>{row.error || job?.message || device.reason}</p>
            {experimental && <p style={textStyle}>Experimental: protocol reviewed; a complete flash has not yet been validated on hardware.</p>}
            {device.backend === "wireless" && <p style={textStyle}>Uses 2.4 GHz through the USB receiver. Charge the controller to at least 30%.</p>}
            {release?.notes && <p style={{ ...textStyle, whiteSpace: "pre-wrap" }}>{release.notes}</p>}
            {job?.evidence && <p style={textStyle}>{job.evidence}</p>}
          </div>}
        </PanelSectionRow>;
      })}
    </PanelSection>

    <PanelSection>
      <PanelSectionRow><ButtonItem layout="below" onClick={() => setAdvanced(!advanced)}>
        {advanced ? "Hide options" : "More options"}
      </ButtonItem></PanelSectionRow>
      {advanced && <>
        <RecoveryAccessSettings disabled={disabled} />
        <ManualFirmware models={updates.models} disabled={disabled} onJob={(id) => updates.watch({ id, state: "queued", progress: 0, can_install: false, message: "Downloading firmware…" })} />
        <ButtonItem disabled={disabled} layout="below" onClick={() => void act(async () => {
          const result = await openOfficialUpdater();
          if (!result.opened) throw new Error(result.error ?? "Could not open the official updater.");
          setNotice("Opened the official updater in your browser.");
        })}>Open official updater</ButtonItem>
        <ButtonItem disabled={disabled} layout="below" onClick={() => void act(async () => {
          const result = await exportDiagnostics();
          if (result.error || !result.path) throw new Error(result.error ?? "Could not export diagnostics.");
          setNotice(`Diagnostics saved to ${result.path}`);
        })}>Export diagnostics</ButtonItem>
        {updates.warnings.map((warning, index) => <p key={index} style={textStyle}>{warning}</p>)}
      </>}
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
