import { useParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/Layout";

export function Account() {
  const { id } = useParams();
  return (
    <>
      <PageHeader title="Account" subtitle={`#${id ?? ""}`} />
      <EmptyState>coming soon</EmptyState>
    </>
  );
}
