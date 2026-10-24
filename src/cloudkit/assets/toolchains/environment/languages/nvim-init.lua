-- Shared, plugin-free cloud editor. Optional LSPs remain per-language capabilities.
vim.opt.number = true
vim.opt.expandtab = true
vim.opt.shiftwidth = 2
vim.opt.tabstop = 2
vim.opt.swapfile = false
vim.opt.undofile = false
vim.opt.shadafile = "NONE"
local root = vim.env.CLOUDKIT_LANGUAGE_ROOT or vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h")
local node = root .. "/node/node_modules/.bin/"
local servers = {
  ts_ls = { cmd = { node .. "typescript-language-server", "--stdio" }, init_options = { tsserver = { path = root .. "/node/node_modules/typescript/lib/tsserver.js" } }, filetypes = { "javascript", "javascriptreact", "typescript", "typescriptreact" }, root_markers = { "tsconfig.json", "package.json", ".git" } },
  pyright = { cmd = { node .. "pyright-langserver", "--stdio" }, filetypes = { "python" }, root_markers = { "pyproject.toml", "requirements.txt", ".git" } },
  html = { cmd = { node .. "vscode-html-language-server", "--stdio" }, filetypes = { "html" }, root_markers = { "package.json", ".git" } },
  cssls = { cmd = { node .. "vscode-css-language-server", "--stdio" }, filetypes = { "css", "scss", "less" }, root_markers = { "package.json", ".git" } },
  jsonls = { cmd = { node .. "vscode-json-language-server", "--stdio" }, filetypes = { "json", "jsonc" }, root_markers = { "package.json", ".git" } },
  yamlls = { cmd = { node .. "yaml-language-server", "--stdio" }, filetypes = { "yaml" }, root_markers = { ".git" } },
  bashls = { cmd = { node .. "bash-language-server", "start" }, filetypes = { "sh", "bash" }, root_markers = { ".git" } },
  gopls = { cmd = { root .. "/bin/gopls" }, filetypes = { "go", "gomod", "gowork" }, root_markers = { "go.work", "go.mod", ".git" } },
  rust_analyzer = { cmd = { root .. "/bin/rust-analyzer" }, filetypes = { "rust" }, root_markers = { "Cargo.toml", ".git" } },
}
for name, settings in pairs(servers) do
  vim.lsp.config(name, settings)
  vim.lsp.enable(name)
end
