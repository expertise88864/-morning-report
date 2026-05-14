@echo off
REM ASCII-only batch file. cmd.exe parses .bat in the system codepage,
REM so non-ASCII text here would corrupt parsing. Keep everything ASCII.
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   Morning Report - commit and push
echo ============================================
echo.

set "FIRST_RUN="
if exist ".git" goto :detect_branch

REM ---------------- first run: set up local repo ----------------
set "FIRST_RUN=1"
echo [setup] First run: initialising git and connecting to GitHub.
git init
echo.
echo Paste your GitHub repo URL (green "Code" button on the repo page,
echo HTTPS address ending with .git):
set /p REPO_URL="Repo URL: "
git remote add origin "%REPO_URL%"
git fetch origin
if errorlevel 1 (
  echo.
  echo [ERROR] git fetch failed. Check the URL and that you are signed in to GitHub.
  pause
  exit /b 1
)

:detect_branch
REM Figure out whether the remote default branch is main or master.
set "REMOTE_BRANCH=main"
git rev-parse --verify origin/main >nul 2>&1
if errorlevel 1 (
  git rev-parse --verify origin/master >nul 2>&1
  if not errorlevel 1 set "REMOTE_BRANCH=master"
)

if not "%FIRST_RUN%"=="1" goto :stage
REM adopt remote history without overwriting your local edited files
git branch -M %REMOTE_BRANCH%
git reset --soft origin/%REMOTE_BRANCH%
REM restore the state/ folder from remote so it is not seen as "deleted"
git checkout -- state 2>nul
echo [setup] Done. Using branch: %REMOTE_BRANCH%
echo.

:stage
echo === Staging files ===
git add morning_report.py requirements.txt README.md .gitignore commit_push.bat
git add tests .github/workflows/morning-report.yml .github/workflows/ci.yml
REM old test script removed; untrack it if still tracked (no error if absent)
git rm --cached --ignore-unmatch test_with_mock.py >nul 2>&1
echo.
git status --short
echo.

set "MSG="
set /p MSG="Commit message (press Enter for default): "
if "%MSG%"=="" set "MSG=chore: update morning report"

git commit -m "%MSG%"
if errorlevel 1 (
  echo.
  echo [INFO] Nothing to commit, or commit failed. See message above.
  pause
  exit /b 0
)

echo.
echo === Sync with remote, then push ===
git pull --rebase origin %REMOTE_BRANCH%
if errorlevel 1 (
  echo.
  echo [ERROR] git pull --rebase hit a conflict. Resolve it, then run this again.
  pause
  exit /b 1
)
git push -u origin %REMOTE_BRANCH%
if errorlevel 1 (
  echo.
  echo [ERROR] git push failed. See message above (usually login or permission).
  pause
  exit /b 1
)

echo.
echo ============================================
echo   Done. Pushed to GitHub. The scheduled
echo   Actions workflow is not affected.
echo ============================================
pause
endlocal
