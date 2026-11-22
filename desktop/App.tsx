import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { preparationAdvice } from "../src/shared/preparation";
import { DevicePicture } from "../src/shared/DevicePicture";
import { jobIsActive, progressValue, theme } from "../src/shared/theme";
import type {
  AppInfo,
  AppUpdate,
  Device,
  Job,
  Model,
  RecoveryAccess,
  Release,
  UpdaterClient,
} from "../src/shared/types";
import { desktopAPI, quitDesktop } from "./bridge";

const LAST_JOB = "8bitdo-updater:last-job";
const MAX_STATUS_RETRIES = 3;
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
const identity = (device: Device) =>
  JSON.stringify([
    device.id,
    device.model_id,
    device.vid,
    device.pid,
    device.role,
  ]);
const authoritative = (device: Device) =>
  !!device.model_id && ["identified", "supported"].includes(device.status);
const readJob = () => {
  try {
    return localStorage.getItem(LAST_JOB) ?? "";
  } catch {
    return "";
  }
};
function rememberJob(id: string) {
  try {
    if (id) localStorage.setItem(LAST_JOB, id);
    else localStorage.removeItem(LAST_JOB);
  } catch {
    /* Browser storage is optional. */
  }
}

function ConfirmDialog({
  title,
  children,
  label,
  busy,
  onCancel,
  onConfirm,
}: {
  title: string;
  children: React.ReactNode;
  label: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="confirmation"
      aria-labelledby="confirmation-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
    >
      <p className="eyebrow">Your confirmation</p>
      <h2 id="confirmation-title">{title}</h2>
      {children}
      <div className="button-row">
        <button autoFocus disabled={busy} onClick={onCancel}>
          Go back
        </button>
        <button className="primary" disabled={busy} onClick={onConfirm}>
          {busy ? "Starting…" : label}
        </button>
      </div>
    </dialog>
  );
}

export function App({ api = desktopAPI }: { api?: UpdaterClient }) {
  const [page, setPage] = useState<"devices" | "catalog" | "settings">(
    "devices",
  );
  const [info, setInfo] = useState<AppInfo | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [selected, setSelected] = useState({
    deviceId: "",
    modelId: "",
    identity: "",
    browsing: false,
  });
  const selection = useRef(selected);
  const scanRevision = useRef(0);
  const selectionRevision = useRef(0);
  const alive = useRef(true);
  const acting = useRef(false);
  const [scanning, setScanning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [search, setSearch] = useState("");
  const [releases, setReleases] = useState<Release[]>([]);
  const [releaseId, setReleaseId] = useState("");
  const [loadingReleases, setLoadingReleases] = useState(false);
  const [jobId, setJobId] = useState(readJob);
  const [job, setJob] = useState<Job | null>(null);
  const [jobError, setJobError] = useState("");
  const [pollRevision, setPollRevision] = useState(0);
  const [access, setAccess] = useState<RecoveryAccess | null>(null);
  const [confirmation, setConfirmation] = useState<
    "install" | "recovery" | "app" | "app-recovery" | null
  >(null);
  const [channel, setChannel] = useState("stable");
  const [appUpdate, setAppUpdate] = useState<AppUpdate | null>(null);
  const active = !!job && jobIsActive(job.state);
  const locked = busy || active || !!jobError;
  const device = devices.find((item) => item.id === selected.deviceId);
  const model = models.find((item) => item.id === selected.modelId);
  const preparation = preparationAdvice(model, device, info?.capabilities?.native_write !== false);
  const release = releases.find((item) => item.id === releaseId);
  const canWrite = info?.capabilities?.native_write !== false;

  const choose = useCallback((next: typeof selected) => {
    selection.current = next;
    setSelected(next);
    setConfirmation(null);
  }, []);
  const chooseDevice = (item: Device) => {
    selectionRevision.current++;
    choose({
      deviceId: item.id,
      modelId: authoritative(item) ? item.model_id! : "",
      identity: identity(item),
      browsing: false,
    });
    setError("");
  };
  const scan = useCallback(async () => {
    const revision = ++scanRevision.current;
    const previousChoice = selectionRevision.current;
    setScanning(true);
    setError("");
    try {
      const [result, catalog] = await Promise.all([
        api.request<{ devices: Device[]; warnings: string[] }>("scan_devices"),
        api.request<Model[]>("get_models"),
      ]);
      if (!alive.current || revision !== scanRevision.current) return;
      setDevices(result.devices);
      setWarnings(result.warnings);
      setModels(catalog);
      const current = selection.current;
      if (current.deviceId) {
        const found = result.devices.find(
          (item) => item.id === current.deviceId,
        );
        if (
          found &&
          authoritative(found) &&
          identity(found) === current.identity
        )
          return;
        if (
          previousChoice === selectionRevision.current ||
          !found ||
          identity(found) !== current.identity
        )
          choose({ deviceId: "", modelId: "", identity: "", browsing: false });
      } else if (
        !current.browsing &&
        previousChoice === selectionRevision.current
      ) {
        const found = result.devices.find(
          (item) =>
            authoritative(item) &&
            catalog.some((entry) => entry.id === item.model_id),
        );
        if (found)
          choose({
            deviceId: found.id,
            modelId: found.model_id!,
            identity: identity(found),
            browsing: false,
          });
      }
    } catch (reason) {
      if (alive.current && revision === scanRevision.current)
        setError(errorText(reason));
    } finally {
      if (alive.current && revision === scanRevision.current)
        setScanning(false);
    }
  }, [api, choose]);

  useEffect(() => {
    alive.current = true;
    void api
      .request<AppInfo>("info")
      .then((result) => {
        if (alive.current) setInfo(result);
      })
      .catch((reason) => {
        if (alive.current) setError(errorText(reason));
      });
    void scan();
    return () => {
      alive.current = false;
      scanRevision.current++;
    };
  }, [api, scan]);

  useEffect(() => {
    let cancelled = false;
    setReleases([]);
    setReleaseId("");
    setLoadingReleases(!!selected.modelId);
    if (selected.modelId)
      void api
        .request<{ releases: Release[] }>("get_releases", {
          model_id: selected.modelId,
        })
        .then((result) => {
          if (!cancelled) {
            setReleases(result.releases);
            setReleaseId(result.releases[0]?.id ?? "");
          }
        })
        .catch((reason) => {
          if (!cancelled) setError(errorText(reason));
        })
        .finally(() => {
          if (!cancelled) setLoadingReleases(false);
        });
    return () => {
      cancelled = true;
    };
  }, [api, selected.modelId]);

  useEffect(() => {
    if (!jobId) {
      setJobError("");
      return;
    }
    if (busy) return;
    let cancelled = false;
    let failures = 0;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await api.request<Job>("get_job", { job_id: jobId });
        if (cancelled) return;
        failures = 0;
        setJobError("");
        setJob(result);
        if (jobIsActive(result.state))
          timer = setTimeout(() => void poll(), 1000);
      } catch (reason) {
        if (cancelled) return;
        failures++;
        const retry = failures <= MAX_STATUS_RETRIES;
        setJobError(
          `Could not read update progress: ${errorText(reason)}. ${retry ? "Retrying status…" : "Automatic retries stopped. Retry status to check the worker again."}`,
        );
        if (retry) timer = setTimeout(() => void poll(), 1000 * 2 ** failures);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [api, jobId, busy, pollRevision]);

  useEffect(() => {
    if (page !== "settings") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await api.request<RecoveryAccess>("get_recovery_access");
        if (cancelled) return;
        setAccess(result);
        if (result.pending) timer = setTimeout(() => void poll(), 1000);
      } catch (reason) {
        if (!cancelled) setError(errorText(reason));
      }
    };
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [api, page, busy]);

  const act = async (action: () => Promise<void>) => {
    if (acting.current) return;
    acting.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (reason) {
      if (alive.current) {
        setConfirmation(null);
        setError(errorText(reason));
      }
    } finally {
      acting.current = false;
      if (alive.current) setBusy(false);
    }
  };
  const prepare = () =>
    void act(async () => {
      const result = await api.request<{ job_id: string }>("prepare_update", {
        model_id: selected.modelId,
        release_id: releaseId,
        device_id:
          device &&
          authoritative(device) &&
          device.model_id === selected.modelId
            ? device.id
            : "",
      });
      if (!result.job_id)
        throw new Error("The backend did not return an update job.");
      if (alive.current) {
        setJob(null);
        setJobId(result.job_id);
        rememberJob(result.job_id);
      }
    });
  const confirm = () =>
    void act(async () => {
      if (confirmation === "app") {
        const result = await api.request<AppUpdate>("apply_app_update", {
          channel,
          version: appUpdate?.available_version,
          confirmed: true,
        });
        if (alive.current) setAppUpdate(result);
      } else if (confirmation === "app-recovery") {
        const result = await api.request<AppUpdate>("recover_app_update", {
          confirmed: true,
        });
        if (alive.current) setAppUpdate(result);
      } else if (job && confirmation) {
        const result = await api.request<Job>(
          confirmation === "install" ? "install_update" : "recover_update",
          { job_id: job.id, confirmed: true },
        );
        if (alive.current) setJob(result);
      }
      if (alive.current) setConfirmation(null);
    });
  const style = Object.fromEntries(
    Object.entries(theme).map(([name, value]) => [
      `--${name}`,
      typeof value === "number" ? `${value}px` : value,
    ]),
  ) as CSSProperties;

  return (
    <div className="app" style={style}>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            8
          </span>
          <span>
            8BitDo<span className="brand-subtitle">UPDATER</span>
          </span>
        </div>
        <nav aria-label="Main navigation">
          <button
            className={page === "devices" ? "nav-item selected" : "nav-item"}
            aria-current={page === "devices" ? "page" : undefined}
            onClick={() => setPage("devices")}
          >
            <span aria-hidden="true">◉</span> My devices{" "}
            <span className="count">{devices.length}</span>
          </button>
          <button
            className={page === "catalog" ? "nav-item selected" : "nav-item"}
            aria-current={page === "catalog" ? "page" : undefined}
            onClick={() => setPage("catalog")}
          >
            <span aria-hidden="true">▦</span> Device library
          </button>
          <button
            className={page === "settings" ? "nav-item selected" : "nav-item"}
            aria-current={page === "settings" ? "page" : undefined}
            onClick={() => setPage("settings")}
          >
            <span aria-hidden="true">⚙</span> App settings
          </button>
        </nav>
        <div className="sidebar-note">
          Controllers & receivers
          <p>
            Each component keeps its own firmware version and update history.
          </p>
        </div>
        <div className="sidebar-footer">
          Independent community project
          <span>
            {info ? `Version ${info.app_version}` : "Connecting to updater…"}
          </span>
        </div>
      </aside>
      <main>
        <header className="page-header">
          <div>
            <p className="eyebrow">
              {page === "settings"
                ? "Make it yours"
                : "Controllers & accessories"}
            </p>
            <h1>
              {page === "devices"
                ? "My devices"
                : page === "catalog"
                  ? "Device library"
                  : "App settings"}
            </h1>
          </div>
          {page !== "settings" && (
            <button
              className="primary scan"
              disabled={scanning || locked}
              onClick={() => void scan()}
            >
              <span aria-hidden="true">↻</span>{" "}
              {scanning ? "Scanning…" : "Scan devices"}
            </button>
          )}
        </header>
        {error && (
          <div className="alert error" role="alert">
            <strong>Something needs attention</strong>
            <p>{error}</p>
            <button className="quiet" onClick={() => setError("")}>
              Dismiss message
            </button>
          </div>
        )}
        {notice && (
          <p className="alert" role="status">
            {notice}
          </p>
        )}
        {jobError && (
          <div className="alert error" role="alert">
            <strong>Update status is temporarily unavailable</strong>
            <p>{jobError}</p>
            <button
              disabled={busy}
              onClick={() => setPollRevision((revision) => revision + 1)}
            >
              Retry status
            </button>
          </div>
        )}
        {info?.capabilities?.native_write === false && (
          <div className="alert">
            <strong>
              Discovery and downloads are available on this platform.
            </strong>
            <p>
              Native firmware installation and recovery are not yet validated
              here. Use a supported Linux host for those operations.
            </p>
          </div>
        )}
        {page !== "settings" && (
          <div className="workspace">
            <section
              className="device-list"
              aria-label={
                page === "devices"
                  ? "Connected devices"
                  : "Supported device catalog"
              }
            >
              <div className="section-heading">
                <h2>{page === "devices" ? "Connected" : "Browse by model"}</h2>
                <span className="muted">
                  {page === "devices"
                    ? `${devices.length} component${devices.length === 1 ? "" : "s"}`
                    : `${models.length} components`}
                </span>
              </div>
              {page === "catalog" && (
                <label className="search">
                  <span className="sr-only">Search device library</span>
                  <input
                    type="search"
                    placeholder="Find a controller or receiver…"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                </label>
              )}
              <div className="cards">
                {page === "devices"
                  ? devices.map((item) => (
                      <button
                        key={item.id}
                        className={`device-card ${selected.deviceId === item.id ? "is-selected" : ""}`}
                        aria-pressed={selected.deviceId === item.id}
                        disabled={locked}
                        onClick={() => chooseDevice(item)}
                      >
                        <span className="card-top">
                          <span className="pill">{item.role}</span>
                          <span className="connection">
                            <span className="status-dot" /> Connected
                          </span>
                        </span>
                        <div className="artwork">
                          <DevicePicture
                            modelId={item.model_id}
                            name={item.name}
                            size={180}
                          />
                        </div>
                        <span className="card-name">{item.name}</span>
                        <span className="card-bottom">
                          {item.firmware_version
                            ? `Firmware ${item.firmware_version}`
                            : "Version not identified"}
                          <span aria-hidden="true">↗</span>
                        </span>
                      </button>
                    ))
                  : models
                      .filter((item) =>
                        `${item.name} ${item.role}`
                          .toLowerCase()
                          .includes(search.toLowerCase()),
                      )
                      .map((item) => (
                        <button
                          key={item.id}
                          className={`device-card ${selected.modelId === item.id ? "is-selected" : ""}`}
                          aria-pressed={selected.modelId === item.id}
                          disabled={locked}
                          onClick={() => {
                            selectionRevision.current++;
                            choose({
                              deviceId: "",
                              modelId: item.id,
                              identity: "",
                              browsing: true,
                            });
                          }}
                        >
                          <span className="card-top">
                            <span className="pill">{item.role}</span>
                            <span className="muted">Catalog</span>
                          </span>
                          <div className="artwork">
                            <DevicePicture
                              modelId={item.id}
                              name={item.name}
                              size={180}
                            />
                          </div>
                          <span className="card-name">{item.name}</span>
                          <span className="card-bottom">
                            Browse firmware<span aria-hidden="true">↗</span>
                          </span>
                        </button>
                      ))}
              </div>
              {page === "devices" && !devices.length && (
                <div className="empty-state">
                  <DevicePicture name="Controller" size={200} />
                  <h2>
                    {scanning
                      ? "Looking for your devices…"
                      : "Let’s connect your first device"}
                  </h2>
                  <p>
                    Connect a controller or its receiver by USB. Each component
                    gets its own firmware entry.
                  </p>
                  <button onClick={() => setPage("catalog")}>
                    Explore the device library
                  </button>
                </div>
              )}
              {page === "catalog" &&
                !models.filter((item) =>
                  `${item.name} ${item.role}`
                    .toLowerCase()
                    .includes(search.toLowerCase()),
                ).length && (
                  <p className="empty-state">
                    {models.length
                      ? "No devices match that search."
                      : "The device catalog is not available yet."}
                  </p>
                )}
              {warnings.map((warning, index) => (
                <p className="alert" key={index}>
                  {warning}
                </p>
              ))}
              <p className="fine-print">
                Support is specific to each component and firmware version.
                Browsing a model does not identify connected hardware.
              </p>
            </section>
            <aside className="details" aria-label="Firmware details">
              <p className="eyebrow">
                {model?.role === "receiver"
                  ? "Receiver firmware"
                  : model?.role === "controller"
                    ? "Controller firmware"
                    : "Firmware details"}
              </p>
              {model ? (
                <>
                  <div className="detail-title">
                    <DevicePicture
                      modelId={model.id}
                      name={model.name}
                      size={86}
                    />
                    <div>
                      <span className="pill">{model.role}</span>
                      <h2>{model.name}</h2>
                    </div>
                  </div>
                  <div className="version-pair">
                    <div>
                      <span>{device ? "Installed" : "Connection"}</span>
                      <strong>
                        {device?.firmware_version ??
                          (device ? "Unknown" : "Browsing only")}
                      </strong>
                    </div>
                    <span aria-hidden="true">→</span>
                    <div>
                      <span>Selected release</span>
                      <strong>{release?.version ?? "—"}</strong>
                    </div>
                  </div>
                  {device?.reason && <p className="muted">{device.reason}</p>}
                  <p>{model.instructions}</p>
                  <p className="support-note">{model.support}</p>
                  <label className="field">
                    Firmware release
                    <select
                      value={releaseId}
                      disabled={locked || loadingReleases || !releases.length}
                      onChange={(event) => {
                        setReleaseId(event.target.value);
                        setConfirmation(null);
                      }}
                    >
                      {releases.length ? (
                        releases.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.version}
                            {item.date ? ` · ${item.date}` : ""}
                          </option>
                        ))
                      ) : (
                        <option value="">
                          {loadingReleases
                            ? "Checking releases…"
                            : "No releases available"}
                        </option>
                      )}
                    </select>
                  </label>
                  {release?.notes && (
                    <div className="release-notes">
                      <h3>What’s changed</h3>
                      <p>{release.notes}</p>
                    </div>
                  )}
                  <button
                    className="primary full-width"
                    disabled={
                      locked || loadingReleases || !release?.downloadable
                    }
                    onClick={prepare}
                  >
                    {preparation.label} <span aria-hidden="true">↓</span>
                  </button>
                  <p className="fine-print">
                    {preparation.description}
                  </p>
                </>
              ) : (
                <div className="details-empty">
                  <span aria-hidden="true">↖</span>
                  <h2>Choose a component</h2>
                  <p>
                    Select a connected device or browse the library to see
                    firmware and release notes.
                  </p>
                  {device && (
                    <p className="support-note">
                      {device.reason ||
                        "This device’s exact model has not been identified."}
                    </p>
                  )}
                </div>
              )}
            </aside>
          </div>
        )}
        {job && (
          <section className="job-panel" aria-labelledby="job-title">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Update activity</p>
                <h2 id="job-title">
                  {job.target_label || "Firmware preparation"}
                </h2>
              </div>
              <span
                className={`pill ${job.state === "complete" ? "success" : ""}`}
              >
                {job.state}
              </span>
            </div>
            <p role="status">{job.message}</p>
            <progress
              aria-label="Firmware update progress"
              max="100"
              value={progressValue(job.progress)}
            />
            {job.evidence_level === "source-reviewed-experimental" && (
              <p className="support-note">
                Experimental update: the protocol has been reviewed, but a
                complete update has not yet been validated on hardware.
              </p>
            )}
            <div className="button-row">
              {job.can_install && canWrite && (
                <button
                  className="primary"
                  disabled={locked}
                  onClick={() => setConfirmation("install")}
                >
                  Review installation
                </button>
              )}
              {job.can_check_recovery && !job.recovery_ready && (
                <button
                  disabled={locked || !canWrite}
                  onClick={() =>
                    void act(async () => {
                      const result = await api.request<Job>("check_recovery", {
                        job_id: job.id,
                      });
                      if (alive.current) setJob(result);
                    })
                  }
                >
                  Check interrupted update
                </button>
              )}
              {job.recovery_ready && (
                <button
                  disabled={locked || !canWrite}
                  onClick={() => setConfirmation("recovery")}
                >
                  Review recovery
                </button>
              )}
              {!active && (
                <button
                  className="quiet"
                  disabled={busy || !!jobError}
                  onClick={() => {
                    setJob(null);
                    setJobId("");
                    rememberJob("");
                    setConfirmation(null);
                  }}
                >
                  Dismiss status
                </button>
              )}
            </div>
          </section>
        )}
        {page === "settings" && (
          <div className="settings-grid">
            <section className="settings-card">
              <p className="eyebrow">The app itself</p>
              <h2>Application updates</h2>
              <p>
                Keep 8BitDo Updater current. Firmware for your controllers is
                managed separately.
              </p>
              <div className="version-pair">
                <div>
                  <span>Installed app</span>
                  <strong>{info?.app_version ?? "Unknown"}</strong>
                </div>
                <div>
                  <span>Available app</span>
                  <strong>
                    {appUpdate?.available_version ?? "Not checked"}
                  </strong>
                </div>
              </div>
              <label className="field">
                Release channel
                <select
                  value={channel}
                  disabled={locked}
                  onChange={(event) => {
                    setChannel(event.target.value);
                    setAppUpdate(null);
                  }}
                >
                  <option value="stable">Stable releases</option>
                  <option value="alpha">Alpha / preview releases</option>
                </select>
              </label>
              <p className="fine-print">
                Preview releases can contain unfinished features. Update
                packages are checked against signed release metadata.
              </p>
              {appUpdate && (
                <div role="status">
                  <p>{appUpdate.message}</p>
                  {appUpdate.release_notes && (
                    <p className="release-notes">{appUpdate.release_notes}</p>
                  )}
                  {appUpdate.restart_required && (
                    <>
                      <strong>
                        {appUpdate.state === "recovered"
                          ? "Reopen the application to use the restored version."
                          : "Quit the app to finish applying this change."}
                      </strong>
                      <div className="button-row">
                        <button
                          className="primary"
                          disabled={locked}
                          onClick={() => void act(quitDesktop)}
                        >
                          {appUpdate.state === "recovered"
                            ? "Quit application"
                            : "Quit to finish updating"}
                        </button>
                      </div>
                    </>
                  )}
                </div>
              )}
              <div className="button-row">
                <button
                  disabled={locked || !info?.operations.check_app_update}
                  onClick={() =>
                    void act(async () => {
                      const result = await api.request<AppUpdate>(
                        "check_app_update",
                        { channel },
                      );
                      if (alive.current) setAppUpdate(result);
                    })
                  }
                >
                  {busy ? "Working…" : "Check for app updates"}
                </button>
                {appUpdate?.can_apply && (
                  <button
                    className="primary"
                    disabled={locked}
                    onClick={() => setConfirmation("app")}
                  >
                    Review app update
                  </button>
                )}
              </div>
              {appUpdate?.can_recover && (
                <button
                  disabled={locked || !info?.operations.recover_app_update}
                  onClick={() => setConfirmation("app-recovery")}
                >
                  Review app update rollback
                </button>
              )}
              {!info?.operations.check_app_update && (
                <p className="fine-print">
                  App update checks are unavailable in this backend build.
                </p>
              )}
            </section>
            <section className="settings-card">
              <p className="eyebrow">Access & support</p>
              <h2>Device recovery access</h2>
              <p role="status">
                {access?.message ?? "Checking host permissions…"}
              </p>
              {access?.can_setup && !access.configured && (
                <button
                  disabled={locked || access.pending}
                  onClick={() =>
                    void act(async () => {
                      const result = await api.request<RecoveryAccess>(
                        "setup_recovery_access",
                        { confirmed: true },
                      );
                      if (alive.current) setAccess(result);
                    })
                  }
                >
                  {access.pending
                    ? "Waiting for authorization…"
                    : "Set up recovery access"}
                </button>
              )}
              <p className="fine-print">
                Permission setup uses your system’s administrator prompt. It
                does not write firmware.
              </p>
              <hr />
              <h3>Need a closer look?</h3>
              <p>
                Export a diagnostic report to help investigate detection or
                update issues.
              </p>
              <div className="button-row">
                <button
                  disabled={locked}
                  onClick={() =>
                    void act(async () => {
                      const result = await api.request<{ path: string }>(
                        "export_diagnostics",
                      );
                      if (alive.current)
                        setNotice(`Diagnostics saved to ${result.path}`);
                    })
                  }
                >
                  Export diagnostics
                </button>
                <button
                  disabled={locked}
                  onClick={() =>
                    void act(async () => {
                      const result = await api.request<{ opened: boolean }>(
                        "open_official_updater",
                      );
                      if (!result.opened)
                        throw new Error("Could not open the official updater.");
                      if (alive.current)
                        setNotice(
                          "Opened the official updater in your browser.",
                        );
                    })
                  }
                >
                  Official web updater ↗
                </button>
              </div>
              <p className="fine-print">
                The official web updater supports selected models and requires a
                compatible browser.
              </p>
            </section>
          </div>
        )}
        <footer className="main-footer">
          <span>Firmware updates require your confirmation.</span>
          <span>Not affiliated with 8BitDo.</span>
        </footer>
      </main>
      {confirmation && (
        <ConfirmDialog
          title={
            confirmation === "app-recovery"
              ? "Roll back the interrupted app update?"
              : confirmation === "app"
                ? "Update this application?"
                : confirmation === "install"
                  ? "Install the prepared firmware?"
                  : "Recover this interrupted update?"
          }
          label={
            confirmation === "app-recovery"
              ? "Confirm app rollback"
              : confirmation === "app"
                ? "Confirm app update"
                : confirmation === "install"
                  ? "Confirm firmware installation"
                  : job?.recovery_action === "reflash"
                    ? "Confirm erase and reinstall"
                    : "Confirm receiver recovery"
          }
          busy={busy}
          onCancel={() => setConfirmation(null)}
          onConfirm={confirm}
        >
          {confirmation === "app-recovery" ? (
            <>
              <p>
                Restore application files from the interrupted app update. This
                does not write firmware.
              </p>
              <p>Close and reopen the application after recovery finishes.</p>
            </>
          ) : confirmation === "app" ? (
            <>
              <p>
                Update 8BitDo Updater from {appUpdate?.current_version} to{" "}
                {appUpdate?.available_version}. This replaces the application,
                not your controller firmware.
              </p>
              <p>
                Wait until all firmware activity is finished. You may need to
                reopen the app afterward.
              </p>
            </>
          ) : (
            <>
              <p>
                <strong>{job?.target_label}</strong>
              </p>
              {confirmation === "recovery" ? (
                <p>
                  {job?.recovery_action === "reflash"
                    ? "Recovery will erase the receiver firmware and reinstall the exact cached image for this interrupted update."
                    : "Recovery may reset the receiver to finish an already committed update."}
                </p>
              ) : (
                <p>
                  The backend will recheck the device and prepared image before
                  writing.
                </p>
              )}
              <p>Keep USB and power connected until verification finishes.</p>
              {job?.evidence && <p className="evidence">{job.evidence}</p>}
            </>
          )}
        </ConfirmDialog>
      )}
    </div>
  );
}
