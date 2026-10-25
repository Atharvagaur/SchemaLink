import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import Overview from './pages/Overview';
import ExperimentRunner from './pages/ExperimentRunner';
import ExecutionMonitor from './pages/ExecutionMonitor';
import ResultsDashboard from './pages/ResultsDashboard';
import LLMSettings from './pages/LLMSettings';

function App() {
  return (
    <Router>
      <Layout>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/runner" element={<ExperimentRunner />} />
          <Route path="/monitor" element={<ExecutionMonitor />} />
          <Route path="/monitor/:runId" element={<ExecutionMonitor />} />
          <Route path="/results" element={<ResultsDashboard />} />
          <Route path="/results/:runId" element={<ResultsDashboard />} />
          <Route path="/llm-settings" element={<LLMSettings />} />
        </Routes>
      </Layout>
    </Router>
  );
}

export default App;
