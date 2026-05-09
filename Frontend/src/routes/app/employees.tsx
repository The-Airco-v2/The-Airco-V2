import { Plus, Trash2, Users } from "lucide-react";
import { useState } from "react";
import { FaceTrainingPanel } from "@/components/employees/FaceTrainingPanel";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useCreateEmployee,
  useDeleteEmployee,
  useDeleteEmployeeEnrollmentData,
  useEmployees,
} from "@/hooks/useEmployees";
import { toast } from "sonner";

export default function EmployeesPage() {
  const { data: employees, isLoading, error } = useEmployees();
  const createEmployee = useCreateEmployee();
  const deleteEmployee = useDeleteEmployee();
  const deleteEnrollmentData = useDeleteEmployeeEnrollmentData();

  const [addOpen, setAddOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);
  const [name, setName] = useState("");
  const [department, setDepartment] = useState("");

  const handleCreate = () => {
    createEmployee.mutate(
      { name, department },
      {
        onSuccess: () => {
          toast.success("Employee added");
          setAddOpen(false);
          setName("");
          setDepartment("");
        },
        onError: () => toast.error("Failed to add employee"),
      },
    );
  };

  const handleDelete = () => {
    if (!deleteTarget) {
      return;
    }
    deleteEmployee.mutate(deleteTarget.id, {
      onSuccess: () => {
        toast.success("Employee removed");
        setDeleteTarget(null);
      },
      onError: () => toast.error("Failed to remove employee"),
    });
  };

  const handleDeleteEnrollmentData = () => {
    if (!deleteTarget) {
      return;
    }
    deleteEnrollmentData.mutate(deleteTarget.id, {
      onSuccess: () => {
        toast.success("Employee enrollment data removed");
        setDeleteTarget(null);
      },
      onError: () => toast.error("Failed to remove enrollment data"),
    });
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Employees"
        description={employees ? `${employees.length} employee${employees.length !== 1 ? "s" : ""}` : ""}
        actions={
          <Button
            onClick={() => setAddOpen(true)}
            className="gap-1.5 bg-sky-500 text-white hover:bg-sky-400"
            size="sm"
          >
            <Plus className="h-4 w-4" /> Add Employee
          </Button>
        }
      />

      {error && (
        <Alert variant="destructive" className="border-red-800 bg-red-950/40">
          <AlertDescription className="text-red-300">Failed to load employees.</AlertDescription>
        </Alert>
      )}

      <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800">
              {[
                "Name",
                "Department",
                "Enrollment",
                "Actions",
              ].map((h) => (
                <th key={h} className="px-5 py-3 text-left text-xs font-medium uppercase tracking-wide text-zinc-500">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <tr key={i} className="border-b border-zinc-800/50">
                  {Array.from({ length: 4 }).map((__, j) => (
                    <td key={j} className="px-5 py-4">
                      <Skeleton className="h-4 w-full bg-zinc-800" />
                    </td>
                  ))}
                </tr>
              ))
            ) : !employees?.length ? (
              <tr>
                <td colSpan={4} className="py-16 text-center">
                  <Users className="mx-auto mb-3 h-8 w-8 text-zinc-700" />
                  <p className="text-sm text-zinc-500">No employees added yet</p>
                </td>
              </tr>
            ) : (
              employees.map((emp) => (
                <tr key={emp.id} className="border-b border-zinc-800/50 transition-colors hover:bg-zinc-800/20">
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-3">
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-700 text-sm font-medium text-zinc-300">
                        {emp.name.charAt(0).toUpperCase()}
                      </div>
                      <span className="font-medium text-zinc-100">{emp.name}</span>
                    </div>
                  </td>
                  <td className="px-5 py-3.5 text-zinc-400">{emp.department || "—"}</td>
                  <td className="px-5 py-3.5">
                    <StatusBadge status={emp.enrollment_status} />
                  </td>
                  <td className="px-5 py-3.5">
                    <div className="flex items-center justify-end gap-2">
                      <Button
                        size="icon"
                        variant="ghost"
                        className="h-7 w-7 text-zinc-500 hover:bg-red-500/10 hover:text-red-400"
                        onClick={() => setDeleteTarget({ id: emp.id, name: emp.name })}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <FaceTrainingPanel employees={employees} />

      <Sheet open={addOpen} onOpenChange={setAddOpen}>
        <SheetContent className="w-[400px] border-zinc-800 bg-zinc-900 text-zinc-50">
          <SheetHeader>
            <SheetTitle className="text-zinc-50">Add Employee</SheetTitle>
          </SheetHeader>
          <div className="mt-6 space-y-4">
            <div className="space-y-1.5">
              <Label className="text-zinc-300">Name</Label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="border-zinc-700 bg-zinc-800 text-zinc-50 focus-visible:ring-sky-500"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-zinc-300">Department</Label>
              <Input
                value={department}
                onChange={(e) => setDepartment(e.target.value)}
                className="border-zinc-700 bg-zinc-800 text-zinc-50 focus-visible:ring-sky-500"
              />
            </div>
          </div>
          <SheetFooter className="mt-8">
            <Button variant="ghost" onClick={() => setAddOpen(false)} className="text-zinc-400">
              Cancel
            </Button>
            <Button
              onClick={handleCreate}
              disabled={!name || createEmployee.isPending}
              className="bg-sky-500 text-white hover:bg-sky-400"
            >
              {createEmployee.isPending ? "Adding…" : "Add Employee"}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <AlertDialog open={!!deleteTarget} onOpenChange={(open: boolean) => !open && setDeleteTarget(null)}>
        <AlertDialogContent className="border-zinc-800 bg-zinc-900">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-zinc-50">Choose what to remove</AlertDialogTitle>
            <AlertDialogDescription className="text-zinc-400">
              <span className="block text-zinc-300">Employee: {deleteTarget?.name}</span>
              <br />
              Select the action below:
              <br /><br />
              <span className="block text-zinc-200">• <strong>Remove data only</strong> clears the face training data, job history, embeddings, templates, sample photos, and export files.</span>
              <span className="block text-zinc-200">• <strong>Remove employee</strong> does everything above and also deletes the employee record.</span>
              <br />
              This action cannot be undone.
              <br /><br />
              If you only want to clear enrollment history but keep the employee in the list, use <strong>Remove data only</strong>.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-zinc-700 bg-transparent text-zinc-300 hover:bg-zinc-800">
              Cancel
            </AlertDialogCancel>
            <Button
              variant="outline"
              onClick={handleDeleteEnrollmentData}
              disabled={!deleteTarget || deleteEnrollmentData.isPending || deleteEmployee.isPending}
              className="border-zinc-700 bg-transparent text-zinc-200 hover:bg-zinc-800 hover:text-zinc-50"
            >
              {deleteEnrollmentData.isPending ? "Removing data…" : "Remove data only"}
            </Button>
            <Button
              onClick={handleDelete}
              disabled={!deleteTarget || deleteEmployee.isPending || deleteEnrollmentData.isPending}
              className="bg-red-600 text-white hover:bg-red-500"
            >
              {deleteEmployee.isPending ? "Removing employee…" : "Remove employee"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
