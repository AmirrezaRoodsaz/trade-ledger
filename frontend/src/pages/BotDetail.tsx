import { Link, useParams } from "react-router-dom";
import { PageHeader } from "../components/Layout";

/** ponytail: a stub on purpose — the Overview/Runs/Timeline/Config/Controls
 * tabs are the next step. The route exists so the fleet cards link somewhere.
 */
export function BotDetail() {
  const { slug } = useParams();
  return (
    <>
      <PageHeader title={slug ?? "Bot"} subtitle="Detail view — coming next." />
      <Link className="text-accent hover:underline" to="/bots">
        ← back to the fleet
      </Link>
    </>
  );
}
