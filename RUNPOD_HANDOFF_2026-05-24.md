# RunPod Debug Handoff — Updated 2026-05-27

This document captures the status of the RunPod GPU stack integration, the changes applied, the active blocker we were stuck on, and the fix that was applied.

---

## 1. Current Status & The Fix Applied

We successfully automated the pod deployment process and solved the network handshake. The GPU pod successfully registers with Tailscale (e.g., `airco-gpu-7` at IP `100.95.249.87`), and the SSH daemon starts.

The bootstrap script was failing at step `[5/5] Starting GPU stack via Podman` with:

```text
[5/5] Starting GPU stack via Podman...
cannot clone: Operation not permitted
Error: cannot re-exec process
```

**Fix applied (2026-05-27):** Added `/etc/containers/containers.conf` with `userns = "host"` and set `_CONTAINERS_USERNS_CONFIGURED=done` + `PODMAN_USERNS=host` in the Dockerfile. This prevents Podman 4.x from attempting the `clone(CLONE_NEWUSER)` re-exec that RunPod's unprivileged kernel blocks. The bootstrap image must be rebuilt via the `Build RunPod Bootstrap Image` GitHub Action.

---

## 2. Technical Root Cause

RunPod community cloud instances are unprivileged containers running inside a Kubernetes cluster. Because of this:
1. **Blocked System Calls:** The host kernel blocks the `clone` system call with the `CLONE_NEWUSER` flag (creating nested user namespaces).
2. **Missing Setuid Helpers:** Helper binaries like `newuidmap` and `newgidmap` cannot configure sub-UIDs because they lack the necessary namespace permissions or root capabilities inside the unprivileged parent container.
3. **Podman Re-Exec behavior:** By default, Podman attempts to fork and re-exec itself into a child user namespace. When `clone` is rejected by the kernel, it prints `cannot clone: Operation not permitted` and exits.

---

## 3. What We Have Tried

To resolve this namespace restriction, we tried:

1. **Podman 3.4.4 (Ubuntu 22.04) + `_CONTAINERS_USERNS_CONFIGURED="done"`:**
   * **Result:** Bypassed the clone check but crashed with a segmentation fault panic (`r.store = nil`).
   * **Reason:** Overriding `_CONTAINERS_USERNS_CONFIGURED` forces Podman to believe namespaces are configured, but in 3.x this bypasses critical storage instantiation, leaving the storage engine unitialized.

2. **Podman 4.9.3 (Ubuntu 24.04) Upgrade:**
   * **Result:** Upgraded the bootstrap base image to Ubuntu 24.04 to get Podman 4.x.
   * **Reason:** In Podman 4.x, the storage engine was rewritten to support single-UID rootless operation without panicking when namespace bypasses are used.

3. **Podman 4.9.3 + `ignore_chown_errors = "true"`:**
   * **Result:** Added to the `/etc/containers/storage.conf` inside the bootstrap container.
   * **Reason:** Tells Podman's storage driver to ignore file chown errors (since we can only write as the single container-level root UID). However, because Podman still attempts to perform a user namespace `re-exec` before hitting the storage layer, it still fails with `cannot clone: Operation not permitted` during the startup phase.

---

## 4. How to Debug (Step-by-Step)

To test configuration changes interactively without waiting for GitHub Actions or a full pod deployment loop:

### Step 1: Ensure the GPU Pod is Running
Start a session in the web application (or click "Resume" on the pod in your RunPod dashboard). This boots the pod and connects it to Tailscale.

### Step 2: SSH into the Hostinger VPS
Open your terminal and SSH into Hostinger:
```bash
ssh root@72.61.239.69
```

### Step 3: Check Tailscale Status & IP
On the Hostinger VPS, check the Tailscale routing table to find the pod's IP:
```bash
tailscale status
```
Look for `airco-gpu-X` (e.g. `airco-gpu-7` at `100.95.249.87`).

### Step 4: SSH into the GPU Pod
SSH from the Hostinger VPS into the GPU pod using Tailscale:
```bash
ssh root@<gpu-pod-tailscale-ip>
# Password is "root" (unless your SSH public key has successfully authorized)
```

### Step 5: Test Podman Overrides Interactively
Once inside the GPU pod container, you can run commands directly:
1. **Check the bootstrap logs:**
   ```bash
   cat /workspace/bootstrap.log
   ```
2. **Test running Podman with Host Namespace override:**
   ```bash
   podman --userns=host info
   ```
3. **Test setting containers.conf configuration:**
   Create or edit `/etc/containers/containers.conf` inside the pod to force host user namespaces:
   ```ini
   [containers]
   userns = "host"
   netns = "host"
   ipcns = "host"
   ```
   Then try:
   ```bash
   podman info
   ```
4. **Test pulling an image:**
   If `podman info` works, check if pulling a lightweight image succeeds:
   ```bash
   podman pull alpine
   ```

---

## 5. Potential Paths Forward

If you decide to resume fixing this in the future, these are the most promising avenues:

1. **Host Namespace Configuration:** Configure `/etc/containers/containers.conf` with `userns = "host"` inside the bootstrap image to prevent Podman from ever attempting to re-exec or clone a namespace.
2. **Alternative Runtimes:** Since the outer container is already unprivileged, investigate using standard rootless Docker (which has different namespace requirements) or a lighter tool like `nerdctl` or `singularity` if Podman's re-exec cannot be disabled.
3. **Privileged Templates:** Check if the RunPod template can be launched in a "privileged" or "nested" container mode that permits user namespace creation.
