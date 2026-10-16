import { Navigate, Route, Routes } from "react-router-dom";
import { isAuthenticated } from "./auth/auth";
import { LoginPage } from "./pages/LoginPage";
import { CallbackPage } from "./pages/CallbackPage";
import { DashboardPage } from "./pages/DashboardPage";
import { ModelConcentrationPage } from "./pages/ModelConcentrationPage";
import { MonthlyReportPage } from "./pages/MonthlyReportPage";
import { AdminCenterPage } from "./pages/AdminCenterPage";

function RequireAuth({ children }: { children: JSX.Element }) {
  return isAuthenticated() ? children : <Navigate to="/login" replace />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/callback" element={<CallbackPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <DashboardPage />
          </RequireAuth>
        }
      />
      <Route
        path="/model-concentration"
        element={
          <RequireAuth>
            <ModelConcentrationPage />
          </RequireAuth>
        }
      />
      <Route
        path="/report"
        element={
          <RequireAuth>
            <MonthlyReportPage />
          </RequireAuth>
        }
      />
      <Route
        path="/admin/*"
        element={
          <RequireAuth>
            <AdminCenterPage />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
