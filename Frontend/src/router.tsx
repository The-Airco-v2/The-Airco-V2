import { createBrowserRouter, Navigate } from "react-router-dom";
import AppLayout from "@/routes/app/layout";
import AlertsPage from "@/routes/app/alerts";
import CamerasPage from "@/routes/app/cameras";
import DashboardPage from "@/routes/app/dashboard";
import EmployeesPage from "@/routes/app/employees";
import IdentityReviewPage from "@/routes/app/identity-review";
import ReportsPage from "@/routes/app/reports";
import SessionsPage from "@/routes/app/sessions";
import AccountInactivePage from "@/routes/auth/account-inactive";
import AccountNotProvisionedPage from "@/routes/auth/account-not-provisioned";
import InactiveTenantPage from "@/routes/auth/inactive-tenant";
import LoginPage from "@/routes/auth/login";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: "dashboard", element: <DashboardPage /> },
      { path: "cameras", element: <CamerasPage /> },
      { path: "live", element: <Navigate to="/cameras" replace /> },
      { path: "reports", element: <ReportsPage /> },
      { path: "identity-review", element: <IdentityReviewPage /> },
      { path: "employees", element: <EmployeesPage /> },
      { path: "sessions", element: <SessionsPage /> },
      { path: "alerts", element: <AlertsPage /> },
    ],
  },
  { path: "/login", element: <LoginPage /> },
  { path: "/account-not-provisioned", element: <AccountNotProvisionedPage /> },
  { path: "/account-inactive", element: <AccountInactivePage /> },
  { path: "/inactive-tenant", element: <InactiveTenantPage /> },
]);
