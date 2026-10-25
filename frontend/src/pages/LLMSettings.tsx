import { useState, useEffect } from 'react';
import {
  Settings, Key, Link, Play, Save, Trash2, Eye, EyeOff, AlertCircle,
  CheckCircle2, RefreshCw,
} from 'lucide-react';
import { llmConfigAPI, type LLMConfig, type TestConnectionRequest } from '@/lib/api';

const PROVIDERS = [
  { value: 'openrouter', label: 'OpenRouter', defaultBaseUrl: 'https://openrouter.ai/api/v1' },
  { value: 'groq', label: 'Groq', defaultBaseUrl: 'https://api.groq.com/openai/v1' },
  { value: 'custom', label: 'Custom OpenAI-compatible', defaultBaseUrl: '' },
];

export default function LLMSettings() {
  const [config, setConfig] = useState<LLMConfig>({
    provider: null,
    model: null,
    base_url: null,
    configured: false,
    tested: false,
    test_status: null,
    test_error: null,
  });
  const [form, setForm] = useState({
    provider: 'openrouter' as string,
    api_key: '',
    base_url: 'https://openrouter.ai/api/v1',
    model: 'openai/gpt-oss-120b',
  });
  const [showKey, setShowKey] = useState(false);
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState({ text: '', type: 'info' as 'info' | 'success' | 'error' });

  const providerInfo = PROVIDERS.find(p => p.value === form.provider) || PROVIDERS[0];

  useEffect(() => {
    llmConfigAPI.getConfig().then((r) => setConfig(r.data)).catch(() => {});
  }, []);

  const handleProviderChange = (value: string) => {
    const info = PROVIDERS.find(p => p.value === value);
    setForm(prev => ({
      ...prev,
      provider: value,
      base_url: info?.defaultBaseUrl || '',
    }));
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setMessage({ text: '', type: 'info' });
    try {
      const res = await llmConfigAPI.saveConfig({
        provider: form.provider,
        api_key: form.api_key,
        base_url: form.base_url,
        model: form.model,
      });
      setConfig(prev => ({ ...prev, configured: true, provider: form.provider, model: form.model, base_url: form.base_url }));
      setMessage({ text: res.data.message, type: 'success' });
    } catch (e: any) {
      setMessage({ text: e.response?.data?.detail || e.message || 'Failed to save configuration.', type: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setMessage({ text: '', type: 'info' });
    try {
      const req: TestConnectionRequest = {
        provider: form.provider,
        api_key: form.api_key,
        base_url: form.base_url,
        model: form.model,
      };
      const res = await llmConfigAPI.testConnection(req);
      if (res.data.ok) {
        setConfig(prev => ({ ...prev, tested: true, test_status: 'ok', test_error: null }));
        setMessage({ text: `Connection successful to ${form.provider} (${form.model}).`, type: 'success' });
      } else {
        setConfig(prev => ({ ...prev, tested: true, test_status: 'failed', test_error: res.data.error ?? null }));
        setMessage({ text: `Connection failed: ${res.data.error}`, type: 'error' });
      }
    } catch (e: any) {
      const errMsg = e.response?.data?.detail || e.message || 'Unknown error';
      setConfig(prev => ({ ...prev, tested: true, test_status: 'failed', test_error: errMsg }));
      setMessage({ text: `Connection failed: ${errMsg}`, type: 'error' });
    } finally {
      setTesting(false);
    }
  };

  const handleClear = async () => {
    setLoading(true);
    setMessage({ text: '', type: 'info' });
    try {
      await llmConfigAPI.clearConfig();
      setConfig({
        provider: null,
        model: null,
        base_url: null,
        configured: false,
        tested: false,
        test_status: null,
        test_error: null,
      });
      setForm({ provider: 'openrouter', api_key: '', base_url: 'https://openrouter.ai/api/v1', model: 'openai/gpt-oss-120b' });
      setMessage({ text: 'Configuration cleared.', type: 'success' });
    } catch (e: any) {
      setMessage({ text: e.response?.data?.detail || e.message || 'Failed to clear configuration.', type: 'error' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">LLM Settings</h1>
        <p className="text-gray-600 mt-1">
          Configure the LLM provider, API key, and model used for benchmark
          experiments. Settings are saved locally and used when launching runs.
        </p>
      </div>

      {message.text && (
        <div
          className={`p-4 rounded-lg border ${
            message.type === 'success'
              ? 'bg-green-50 border-green-200 text-green-800'
              : message.type === 'error'
                ? 'bg-red-50 border-red-200 text-red-800'
                : 'bg-blue-50 border-blue-200 text-blue-800'
          }`}
        >
          {message.type === 'success' ? (
            <CheckCircle2 className="w-4 h-4 mr-2" />
          ) : message.type === 'error' ? (
            <AlertCircle className="w-4 h-4 mr-2" />
          ) : (
            <InfoCircle className="w-4 h-4 mr-2" />
          )}
          {message.text}
        </div>
      )}

      {/* Config status */}
      <div className="bg-white p-5 rounded-xl border border-gray-200 shadow-sm">
        <div className="flex items-center gap-3 mb-4">
          <Settings className="w-5 h-5 text-gray-500" />
          <h3 className="font-semibold text-gray-900">Current Configuration</h3>
        </div>
        <div className="space-y-3">
          <div className="flex items-center justify-between p-3 rounded border border-gray-200 bg-gray-50">
            <span className="text-sm text-gray-600">Status</span>
            <span className={`text-xs font-medium px-2 py-0.5 rounded ${
              config.configured && config.test_status === 'ok'
                ? 'bg-green-100 text-green-700'
                : config.configured && config.test_status === 'failed'
                  ? 'bg-red-100 text-red-700'
                  : config.configured
                    ? 'bg-amber-100 text-amber-700'
                    : 'bg-gray-100 text-gray-500'
            }`}>
              {config.configured && config.test_status === 'ok' ? 'Configured & tested' :
               config.configured && config.test_status === 'failed' ? 'Test failed' :
               config.configured ? 'Saved, not tested' : 'Not configured'}
            </span>
          </div>
          {config.provider && (
            <div className="flex items-center justify-between p-3 rounded border border-gray-200 bg-gray-50">
              <span className="text-sm text-gray-600">Provider</span>
              <span className="text-sm text-gray-900 font-mono">{config.provider}</span>
            </div>
          )}
          {config.model && (
            <div className="flex items-center justify-between p-3 rounded border border-gray-200 bg-gray-50">
              <span className="text-sm text-gray-600">Model</span>
              <span className="text-sm text-gray-900 font-mono">{config.model}</span>
            </div>
          )}
          {config.base_url && (
            <div className="flex items-center justify-between p-3 rounded border border-gray-200 bg-gray-50">
              <span className="text-sm text-gray-600">Base URL</span>
              <span className="text-sm text-gray-900 font-mono truncate max-w-xs">{config.base_url}</span>
            </div>
          )}
          {config.test_error && (
            <div className="p-3 rounded border border-red-200 bg-red-50">
              <span className="text-xs text-red-700 font-mono">{config.test_error}</span>
            </div>
          )}
        </div>
      </div>

      {/* Configuration form */}
      <form onSubmit={handleSave} className="bg-white p-6 rounded-xl border border-gray-200 shadow-sm space-y-5">
        <div className="space-y-2">
          <label className="flex items-center gap-2 font-medium text-gray-700">
            <Settings className="w-4 h-4" />
            Provider
          </label>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            {PROVIDERS.map((p) => (
              <button
                key={p.value}
                type="button"
                onClick={() => handleProviderChange(p.value)}
                className={`flex flex-col items-center gap-1 p-3 rounded border ${
                  form.provider === p.value
                    ? 'bg-primary-50 border-primary-200 text-primary-700'
                    : 'bg-gray-50 border-gray-200 text-gray-600 hover:border-gray-300'
                }`}
              >
                <span className="text-sm font-medium">{p.label}</span>
                <span className="text-xs text-gray-500">{p.value}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-2">
          <label className="flex items-center gap-2 font-medium text-gray-700">
            <Key className="w-4 h-4" />
            API Key
          </label>
          <div className="relative">
            <input
              type={showKey ? 'text' : 'password'}
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              placeholder="sk-or-v1-..."
              className={`w-full pl-10 pr-10 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent ${showKey ? '' : 'pr-12'}`}
            />
            <div className="absolute left-3 top-3">
              <span className="text-gray-500">sk…</span>
            </div>
            <button
              type="button"
              onClick={() => setShowKey(!showKey)}
              className="absolute right-3 top-3 text-gray-400 hover:text-gray-600"
              aria-label={showKey ? 'Hide API key' : 'Show API key'}
            >
              {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>
          <p className="text-xs text-gray-500">
            Your API key is saved locally only and is never transmitted to the
            browser from the server.
          </p>
        </div>

        <div className="space-y-2">
          <label className="flex items-center gap-2 font-medium text-gray-700">
            <Link className="w-4 h-4" />
            Base URL
          </label>
          <input
            type="url"
            value={form.base_url}
            onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            placeholder="https://api.example.com/v1"
            className="w-full pl-3 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            disabled={form.provider !== 'custom'}
          />
          <p className="text-xs text-gray-500">
            {form.provider === 'custom'
              ? 'Enter the base URL for your OpenAI-compatible provider.'
              : `Default for ${providerInfo.label}. Switch to Custom to edit.`}
          </p>
        </div>

        <div className="space-y-2">
          <label className="flex items-center gap-2 font-medium text-gray-700">
            <Play className="w-4 h-4" />
            Model
          </label>
          <input
            type="text"
            value={form.model}
            onChange={(e) => setForm({ ...form, model: e.target.value })}
            placeholder="e.g. openai/gpt-oss-120b"
            className="w-full pl-3 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent font-mono text-sm"
          />
          <p className="text-xs text-gray-500">
            Enter any model identifier compatible with your provider (e.g.
            <span className="font-mono text-xs bg-gray-100 px-1">openai/gpt-oss-120b</span>).
          </p>
        </div>

        <div className="flex gap-3 pt-2">
          <button
            type="button"
            onClick={handleTest}
            disabled={testing || !form.api_key || !form.base_url || !form.model}
            className={`px-5 py-2.5 rounded-lg font-medium transition ${
              testing
                ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                : 'bg-blue-600 text-white hover:bg-blue-700'
            } flex items-center gap-2`}
          >
            {testing ? (
              <>
                <RefreshCw className="w-4 h-4 animate-spin" />
                Testing…
              </>
            ) : (
              <>
                <Link className="w-4 h-4" />
                Test Connection
              </>
            )}
          </button>
          <button
            type="submit"
            disabled={loading || !form.api_key || !form.base_url || !form.model}
            className={`px-5 py-2.5 rounded-lg font-medium transition ${
              loading
                ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                : 'bg-primary-600 text-white hover:bg-primary-700'
            } flex items-center gap-2`}
          >
            {loading ? (
              <Save className="w-4 h-4 animate-spin" />
            ) : (
              <>
                <Save className="w-4 h-4" />
                Save Settings
              </>
            )}
          </button>
          {config.configured && (
            <button
              type="button"
              onClick={handleClear}
              disabled={loading}
              className={`px-5 py-2.5 rounded-lg font-medium transition ${
                loading
                  ? 'bg-gray-300 text-gray-500 cursor-not-allowed'
                  : 'bg-red-600 text-white hover:bg-red-700'
              } flex items-center gap-2`}
            >
              <Trash2 className="w-4 h-4" />
              Clear Settings
            </button>
          )}
        </div>
      </form>

      <div className="bg-amber-50 border border-amber-200 rounded-xl p-5">
        <h3 className="font-semibold text-amber-900 mb-2">How it works</h3>
        <ul className="text-sm text-amber-800 space-y-2 list-disc pl-5">
          <li>Settings are saved to <code className="font-mono text-xs">llm_config.json</code> in the project root.</li>
          <li>When you launch an experiment, the API key, model, and base URL are passed to the benchmark subprocess.</li>
          <li>The original <code className="font-mono text-xs">config.yaml</code> is never modified.</li>
          <li>If no settings are saved, the backend falls back to <code className="font-mono text-xs">LLM_API_KEY</code> and <code className="font-mono text-xs">GENERATOR_MODEL_SLUG</code> environment variables.</li>
        </ul>
      </div>
    </div>
  );
}

// InfoCircle used for info message type
function InfoCircle({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <circle cx="12" cy="12" r="10" />
      <path d="M12 16v-4" />
      <path d="M12 8h.01" />
    </svg>
  );
}
