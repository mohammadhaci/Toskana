import { useTranslation } from "react-i18next";

import { Card } from "../components/ui";

export default function HelpPage() {
  const { t } = useTranslation();

  return (
    <div className="grid help" style={{ maxWidth: 940 }}>
      <Card>
        <p className="help-lead">{t("help.intro")}</p>
      </Card>

      <Card title={`🎯 ${t("help.stage1Title")}`}>
        <p>{t("help.stage1Body")}</p>
      </Card>

      <Card title={`✨ ${t("help.stage2Title")}`}>
        <p>{t("help.stage2Body")}</p>
      </Card>

      <Card title={t("help.who")}>
        <p>{t("help.whoBody")}</p>
        <div className="table-wrap" style={{ marginTop: "var(--s3)" }}>
          <table className="data">
            <thead>
              <tr>
                <th>{t("help.tableAction")}</th>
                <th>{t("help.tableWho")}</th>
                <th>{t("help.tableAlways")}</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>{t("help.rowCountAction")}</td>
                <td>{t("help.rowCountWho")}</td>
                <td>{t("help.rowCountAlways")}</td>
              </tr>
              <tr>
                <td>{t("help.rowRefineAction")}</td>
                <td>{t("help.rowRefineWho")}</td>
                <td>{t("help.rowRefineAlways")}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="help-note">{t("help.note")}</p>
        <p className="help-note">{t("help.settingsLink")}</p>
      </Card>
    </div>
  );
}
