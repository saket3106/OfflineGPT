# First private GitHub upload with GitHub Desktop

Nothing has been uploaded by this preparation. The staged folder is not yet a Git repository and has no remote. Only continue after reviewing its contents and the preparation report. Do not add the large original project or the separate private audit-report folder.

## 1. Install and sign in

Install [GitHub Desktop](https://desktop.github.com/) and sign in to your GitHub account. Git stores version history on your PC; GitHub is the hosting service. A **commit** is a local saved version. **Publish/push** sends committed files to GitHub.

## 2. Create a local repository in the staged folder

The simplest path avoids creating a second nested folder:

1. Open the staged folder in File Explorer. Open PowerShell there.
2. If Git is installed, run `git init` in **this staged folder only**. This creates local `.git` metadata; it does not upload anything.
3. In GitHub Desktop choose **File → Add local repository → Choose**, select this exact staged folder, then **Add repository**.

If `git` is not available in PowerShell, use Desktop's **File → Add local repository**, select the staged folder, and follow its **create a repository here** option when it reports no Git repository. Verify the resulting local repository path is the existing staging folder, not a new nested directory. Keep the existing README and `.gitignore`; do not ask Desktop to generate replacements or choose a license automatically.

Official reference: [add a local repository](https://docs.github.com/en/desktop/adding-and-cloning-repositories/adding-a-repository-from-your-local-computer-to-github-desktop).

## 3. Review before the first commit

- The file list should contain Python source, frontend source/lockfile, tests, curated icons, documentation and safe templates.
- It must not contain `.sqlite3`, chats, private documents/images, `offline_gpt_config.json`, model weights, `.exe`/`.dll`, logs, environments, `node_modules`, rollback ZIPs or the preparation inventory.
- Review code changes and screenshot content. `.gitignore` helps prevent accidents but does not detect every secret and cannot remove previously committed files from history.
- Enter a summary such as **Initial private OfflineGPT source import**, then click **Commit to main** (or the branch name Desktop displays).

## 4. Publish privately — only when you decide to upload

1. Click **Publish repository**.
2. Choose a repository name, for example **OfflineGPT**. This can differ from the local staging folder's name.
3. Keep **Keep this code private** **checked**. Choose your personal account; do not select an organization unless intended.
4. Review the name and privacy checkbox again, then click **Publish repository**.
5. Use **Repository → View on GitHub** and confirm the repository displays **Private**.

Official reference: [publish an existing project](https://docs.github.com/en/desktop/adding-and-cloning-repositories/adding-an-existing-project-to-github-using-github-desktop). GitHub warns for files over 50 MiB and blocks ordinary Git files over 100 MiB; this source export does not need Git LFS. See [large-file limits](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).

## 5. Later changes

Work in a development checkout, review changes, commit locally, then use **Push origin** when ready to upload. Fetch/pull before working from another computer. Keep database/model/media backups outside Git. Never drag the installed application over the staging folder to “sync everything.” Promote specific reviewed source changes instead. GitHub is not a replacement for your production rollback/data backup system.

Use the GitHub account privacy/email settings if you want commits to use a GitHub-provided no-reply email. Grant collaborator access deliberately. Do not make the repository public until privacy, ownership and licensing have been reviewed again.
