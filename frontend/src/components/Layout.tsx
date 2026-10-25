import { Link, useLocation } from 'react-router-dom';
import { 
  Home, 
  Play, 
  Activity, 
  BarChart3, 
  Settings
} from 'lucide-react';

const navigation = [
  { name: 'Overview', href: '/', icon: Home },
  { name: 'Experiment Runner', href: '/runner', icon: Play },
  { name: 'Execution Monitor', href: '/monitor', icon: Activity },
  { name: 'Results Dashboard', href: '/results', icon: BarChart3 },
  { name: 'LLM Settings', href: '/llm-settings', icon: Settings },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  const location = useLocation();

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="flex h-screen">
        {/* Sidebar */}
        <div className="w-64 bg-white shadow-sm border-r border-gray-200">
          <div className="flex flex-col h-full">
            <div className="flex items-center h-16 px-6 border-b border-gray-200">
              <h1 className="text-xl font-bold text-gray-900">SchemaLink</h1>
            </div>
            <nav className="flex-1 px-4 py-4 space-y-1">
              {navigation.map((item) => {
                const isActive = location.pathname === item.href;
                return (
                  <Link
                    key={item.name}
                    to={item.href}
                    className={`group flex items-center px-2 py-2 text-sm font-medium rounded-md ${
                      isActive
                        ? 'bg-primary-50 text-primary-700'
                        : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                    }`}
                  >
                    <item.icon
                      className={`mr-3 h-5 w-5 ${
                        isActive ? 'text-primary-500' : 'text-gray-400 group-hover:text-gray-500'
                      }`}
                    />
                    {item.name}
                  </Link>
                );
              })}
            </nav>
          </div>
        </div>

        {/* Main content */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <header className="bg-white shadow-sm border-b border-gray-200">
            <div className="px-6 py-4">
              <h2 className="text-lg font-semibold text-gray-900">
                Budget-Aware Schema Linking for Text-to-SQL
              </h2>
            </div>
          </header>
          <main className="flex-1 overflow-y-auto px-6 py-6">
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
