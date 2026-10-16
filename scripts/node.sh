# Sourced by the other scripts: switch to the Node version in .nvmrc when nvm is installed.
if [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
  . "${NVM_DIR:-$HOME/.nvm}/nvm.sh" --no-use
  nvm use --silent >/dev/null || { echo "Node $(cat .nvmrc) not installed: run 'nvm install'"; exit 1; }
fi
