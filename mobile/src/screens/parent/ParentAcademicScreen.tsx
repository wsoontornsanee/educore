/**
 * Parent Academic Tab Screen (spec/08 §2, PAR-010, PAR-015, PAR-016, ACD-013/014).
 * 
 * Displays:
 * 1. Nilai (Grades & Attainment — strictly published assessments only per PAR-010)
 * 2. Tugas (Homework assignments & submission status)
 * 3. Rapor (Semester report cards & arrears gate withholding)
 * 4. Jadwal (Weekly timetable slots & teacher substitutions)
 * 
 * Features 5 mandatory screen states: LOADING, EMPTY, STALE, OFFLINE, ERROR with retry CTA.
 */
import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Linking,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { StaleOfflineBanner } from '../../components/StaleOfflineBanner.tsx';
import {
  fetchStudentGrades,
  fetchStudentHomework,
  fetchStudentReportCards,
  fetchStudentTimetable,
} from '../../services/academic.ts';
import { colors, radius, spacing, typography } from '../../theme/tokens.ts';
import { useLocale } from '../../i18n/LocaleContext.tsx';
import type {
  AcademicSubTab,
  ChildSummary,
  StudentGradesData,
  StudentHomeworkItem,
  StudentReportCardItem,
  StudentTimetableSlotItem,
} from '../../types/index.ts';

interface ParentAcademicScreenProps {
  child: ChildSummary;
  onNavigateInvoices?: () => void;
}

export const ParentAcademicScreen: React.FC<ParentAcademicScreenProps> = ({
  child,
  onNavigateInvoices,
}) => {
  const { t, locale } = useLocale();
  const [activeTab, setActiveTab] = useState<AcademicSubTab>('GRADES');

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isOffline, setIsOffline] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  // Sub-tab specific data
  const [gradesData, setGradesData] = useState<StudentGradesData | null>(null);
  const [homeworkList, setHomeworkList] = useState<StudentHomeworkItem[]>([]);
  const [reportCards, setReportCards] = useState<StudentReportCardItem[]>([]);
  const [timetableSlots, setTimetableSlots] = useState<StudentTimetableSlotItem[]>([]);
  const [selectedDay, setSelectedDay] = useState<number>(1); // 1 = Senin

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      if (activeTab === 'GRADES') {
        const res = await fetchStudentGrades(child.student_id);
        setGradesData(res.data);
        setIsOffline(res.isOfflineCached);
        setLastUpdated(res.lastUpdated);
      } else if (activeTab === 'HOMEWORK') {
        const res = await fetchStudentHomework(child.student_id);
        setHomeworkList(res.homework);
        setIsOffline(res.isOfflineCached);
        setLastUpdated(res.lastUpdated);
      } else if (activeTab === 'REPORT_CARDS') {
        const res = await fetchStudentReportCards(child.student_id);
        setReportCards(res.reportCards);
        setIsOffline(res.isOfflineCached);
        setLastUpdated(res.lastUpdated);
      } else if (activeTab === 'TIMETABLE') {
        const res = await fetchStudentTimetable(child.student_id);
        setTimetableSlots(res.slots);
        setIsOffline(res.isOfflineCached);
        setLastUpdated(res.lastUpdated);
      }
    } catch (err: any) {
      setError(err?.message || t('common.error'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [child.student_id, activeTab]);

  const subTabs: Array<{ key: AcademicSubTab; label: string }> = [
    { key: 'GRADES', label: t('academic.tab_grades') },
    { key: 'HOMEWORK', label: t('academic.tab_homework') },
    { key: 'REPORT_CARDS', label: t('academic.tab_reports') },
    { key: 'TIMETABLE', label: t('academic.tab_timetable') },
  ];

  const renderTabSelector = () => (
    <View style={styles.tabBar} accessibilityRole="tablist">
      {subTabs.map((tab) => {
        const isActive = activeTab === tab.key;
        return (
          <TouchableOpacity
            key={tab.key}
            style={[styles.tabButton, isActive && styles.tabButtonActive]}
            onPress={() => setActiveTab(tab.key)}
            accessibilityRole="tab"
            accessibilityState={{ selected: isActive }}
            accessibilityLabel={tab.label}
          >
            <Text style={[styles.tabText, isActive && styles.tabTextActive]}>
              {tab.label}
            </Text>
          </TouchableOpacity>
        );
      })}
    </View>
  );

  const renderGradesContent = () => {
    const subjects = gradesData?.subjects || [];
    if (subjects.length === 0) {
      return (
        <View style={styles.emptyContainer}>
          <Text style={styles.emptyTitle}>{t('academic.empty_grades_title')}</Text>
          <Text style={styles.emptySubtitle}>
            {t('academic.empty_grades')}
          </Text>
        </View>
      );
    }

    return (
      <View style={styles.listContainer}>
        {subjects.map((subj) => (
          <View key={subj.class_subject_id} style={styles.card}>
            <View style={styles.cardHeader}>
              <View style={styles.cardHeaderLeft}>
                <Text style={styles.subjectName}>{subj.subject_name}</Text>
                <Text style={styles.subjectMeta}>
                  {subj.subject_code} • {subj.class_group_name} • {subj.teacher_name || 'Guru Pengampu'}
                </Text>
              </View>
              {subj.final_grade !== null && (
                <View style={styles.gradeBadge}>
                  <Text style={styles.gradeBadgeScore}>{subj.final_grade}</Text>
                  {subj.final_descriptor ? (
                    <Text style={styles.gradeBadgeDesc}>{subj.final_descriptor}</Text>
                  ) : null}
                </View>
              )}
            </View>

            {subj.assessments.length === 0 ? (
              <Text style={styles.emptyInnerNote}>{t('academic.empty_grades')}</Text>
            ) : (
              <View style={styles.assessmentList}>
                {subj.assessments.map((a) => (
                  <View key={a.id} style={styles.assessmentItem}>
                    <View style={styles.assessmentMain}>
                      <View style={styles.assessmentTitleRow}>
                        <Text style={styles.assessmentTitle}>{a.title}</Text>
                        <View style={styles.typeTag}>
                          <Text style={styles.typeTagText}>{a.type_display || a.type}</Text>
                        </View>
                      </View>
                      <Text style={styles.assessmentMeta}>
                        {t('academic.weight')} {parseFloat(a.weight || '0')}% • {t('academic.max')} {a.max_score}
                      </Text>
                      {a.feedback ? (
                        <Text style={styles.feedbackText}>{t('academic.feedback')} {a.feedback}</Text>
                      ) : null}
                    </View>
                    <View style={styles.assessmentScoreBox}>
                      <Text style={styles.assessmentScoreText}>
                        {a.score !== null ? a.score : '-'}
                      </Text>
                      {a.descriptor ? (
                        <Text style={styles.assessmentDescriptorText}>{a.descriptor}</Text>
                      ) : null}
                    </View>
                  </View>
                ))}
              </View>
            )}
          </View>
        ))}
      </View>
    );
  };

  const renderHomeworkContent = () => {
    if (homeworkList.length === 0) {
      return (
        <View style={styles.emptyContainer}>
          <Text style={styles.emptyTitle}>{t('academic.empty_homework_title')}</Text>
          <Text style={styles.emptySubtitle}>{t('academic.empty_homework')}</Text>
        </View>
      );
    }

    const getStatusDetails = (status: string, score: string | null) => {
      switch (status) {
        case 'GRADED':
          return {
            label: score ? `${t('academic.status_graded')}: ${score}` : t('academic.status_graded'),
            bg: colors.sakitLight,
            color: colors.sakit,
          };
        case 'SUBMITTED':
          return { label: t('academic.status_submitted'), bg: colors.hadirLight, color: colors.hadir };
        case 'LATE':
          return { label: t('academic.status_late'), bg: colors.alpaLight, color: colors.alpa };
        case 'RETURNED':
          return { label: t('academic.status_returned'), bg: colors.substituteLight, color: colors.substitute };
        default:
          return { label: t('academic.status_pending'), bg: colors.izinLight, color: colors.izin };
      }
    };

    return (
      <View style={styles.listContainer}>
        {homeworkList.map((hw) => {
          const statusInfo = getStatusDetails(hw.submission_status, hw.score);
          const dueDateFormatted = hw.due_at
            ? new Date(hw.due_at).toLocaleDateString(locale, {
                day: 'numeric',
                month: 'short',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
              })
            : '-';

          return (
            <View key={hw.id} style={styles.card}>
              <View style={styles.cardHeader}>
                <View style={styles.cardHeaderLeft}>
                  <Text style={styles.subjectName}>{hw.title}</Text>
                  <Text style={styles.subjectMeta}>
                    {hw.subject_name} • {hw.teacher_name}
                  </Text>
                </View>
                <View style={[styles.statusPill, { backgroundColor: statusInfo.bg }]}>
                  <Text style={[styles.statusPillText, { color: statusInfo.color }]}>
                    {statusInfo.label}
                  </Text>
                </View>
              </View>

              {hw.instructions ? (
                <Text style={styles.instructionsText}>{hw.instructions}</Text>
              ) : null}

              <View style={styles.hwFooter}>
                <Text style={styles.dueText}>{t('academic.due_date')} {dueDateFormatted}</Text>
                {hw.files_count > 0 && (
                  <Text style={styles.filesCountText}>{hw.files_count} {t('academic.files_attached')}</Text>
                )}
              </View>

              {hw.feedback ? (
                <View style={styles.feedbackBox}>
                  <Text style={styles.feedbackLabel}>{t('academic.teacher_feedback')}</Text>
                  <Text style={styles.feedbackContent}>{hw.feedback}</Text>
                </View>
              ) : null}
            </View>
          );
        })}
      </View>
    );
  };

  const dayNames = [
    t('day.1'),
    t('day.2'),
    t('day.3'),
    t('day.4'),
    t('day.5'),
    t('day.6'),
    t('day.0'),
  ];

  const renderReportCardsContent = () => {
    if (reportCards.length === 0) {
      return (
        <View style={styles.emptyContainer}>
          <Text style={styles.emptyTitle}>{t('academic.empty_reports_title')}</Text>
          <Text style={styles.emptySubtitle}>
            {t('academic.empty_reports')}
          </Text>
        </View>
      );
    }

    return (
      <View style={styles.listContainer}>
        {reportCards.map((rc) => {
          if (!rc.visible && rc.reason === 'ARREARS') {
            return (
              <View key={rc.id} style={[styles.card, styles.arrearsCard]}>
                <View style={styles.arrearsHeader}>
                  <Text style={styles.arrearsTitle}>{t('academic.arrears_title')}</Text>
                  <Text style={styles.arrearsTerm}>
                    {rc.term_name} • {rc.academic_year_name}
                  </Text>
                </View>
                <Text style={styles.arrearsDesc}>
                  {t('academic.arrears_notice')}
                </Text>
                {onNavigateInvoices ? (
                  <TouchableOpacity
                    style={styles.payButton}
                    onPress={onNavigateInvoices}
                    accessibilityRole="button"
                    accessibilityLabel="Buka menu tagihan untuk menyelesaikan pembayaran"
                  >
                    <Text style={styles.payButtonText}>{t('academic.view_pay_btn')}</Text>
                  </TouchableOpacity>
                ) : null}
              </View>
            );
          }

          const att = rc.attendance_snapshot || {};
          return (
            <View key={rc.id} style={styles.card}>
              <View style={styles.cardHeader}>
                <View style={styles.cardHeaderLeft}>
                  <Text style={styles.subjectName}>{rc.term_name}</Text>
                  <Text style={styles.subjectMeta}>{rc.academic_year_name}</Text>
                </View>
                <View style={[styles.statusPill, { backgroundColor: colors.hadirLight }]}>
                  <Text style={[styles.statusPillText, { color: colors.hadir }]}>{t('academic.published')}</Text>
                </View>
              </View>

              {/* Attendance Snapshot */}
              <View style={styles.attendanceRow}>
                <Text style={styles.attendanceItem}>{t('academic.hadir')}: {att.hadir ?? 0}</Text>
                <Text style={styles.attendanceItem}>{t('academic.sakit')}: {att.sakit ?? 0}</Text>
                <Text style={styles.attendanceItem}>{t('academic.izin')}: {att.izin ?? 0}</Text>
                <Text style={styles.attendanceItem}>{t('academic.alpa')}: {att.alpa ?? 0}</Text>
              </View>

              {/* Grades Summary Snapshot */}
              {rc.grades_snapshot && rc.grades_snapshot.length > 0 && (
                <View style={styles.gradesSnapshotTable}>
                  {rc.grades_snapshot.slice(0, 5).map((g, idx) => (
                    <View key={idx} style={styles.snapshotRow}>
                      <Text style={styles.snapshotSubject} numberOfLines={1}>
                        {g.subject}
                      </Text>
                      <Text style={styles.snapshotGrade}>{g.grade}</Text>
                    </View>
                  ))}
                  {rc.grades_snapshot.length > 5 && (
                    <Text style={styles.moreSubjectsText}>
                      +{rc.grades_snapshot.length - 5} mata pelajaran lainnya
                    </Text>
                  )}
                </View>
              )}

              {rc.download_url ? (
                <TouchableOpacity
                  style={styles.downloadButton}
                  onPress={() => rc.download_url && Linking.openURL(rc.download_url)}
                  accessibilityRole="button"
                  accessibilityLabel="Unduh salinan resmi rapor PDF"
                >
                  <Text style={styles.downloadButtonText}>{t('academic.download_rapor')}</Text>
                </TouchableOpacity>
              ) : null}
            </View>
          );
        })}
      </View>
    );
  };

  const renderTimetableContent = () => {
    if (timetableSlots.length === 0) {
      return (
        <View style={styles.emptyContainer}>
          <Text style={styles.emptyTitle}>{t('academic.empty_timetable_title')}</Text>
          <Text style={styles.emptySubtitle}>
            {t('academic.empty_timetable')}
          </Text>
        </View>
      );
    }

    const filteredSlots = timetableSlots.filter((s) => s.day_of_week === selectedDay);

    return (
      <View style={styles.listContainer}>
        {/* Day Selector Chips */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={styles.daySelector}
          contentContainerStyle={styles.daySelectorContent}
        >
          {dayNames.slice(0, 6).map((dayName, idx) => {
            const dayNum = idx + 1;
            const isDayActive = selectedDay === dayNum;
            return (
              <TouchableOpacity
                key={dayNum}
                style={[styles.dayChip, isDayActive && styles.dayChipActive]}
                onPress={() => setSelectedDay(dayNum)}
                accessibilityRole="button"
                accessibilityLabel={`Pilih hari ${dayName}`}
              >
                <Text style={[styles.dayChipText, isDayActive && styles.dayChipTextActive]}>
                  {dayName}
                </Text>
              </TouchableOpacity>
            );
          })}
        </ScrollView>

        {filteredSlots.length === 0 ? (
          <View style={styles.emptyDayBox}>
            <Text style={styles.emptyDayText}>
              {t('academic.empty_timetable')}
            </Text>
          </View>
        ) : (
          filteredSlots.map((slot) => (
            <View key={slot.id} style={styles.slotCard}>
              <View style={styles.slotTimeBox}>
                <Text style={styles.slotPeriod}>{t('academic.period_no')}{slot.period_no}</Text>
                <Text style={styles.slotTime}>
                  {slot.start_time} - {slot.end_time}
                </Text>
              </View>

              <View style={styles.slotInfoBox}>
                <Text style={styles.slotSubject}>{slot.subject_name}</Text>
                <Text style={styles.slotMeta}>
                  {slot.room ? `Ruang: ${slot.room}` : 'Ruang Kelas'} • {slot.teacher_name}
                </Text>
                {slot.is_substituted && (
                  <View style={styles.substituteBadge}>
                    <Text style={styles.substituteBadgeText}>
                      {t('academic.substitute')} {slot.substitute_teacher_name || 'Ditugaskan'}
                    </Text>
                  </View>
                )}
              </View>
            </View>
          ))
        )}
      </View>
    );
  };

  return (
    <View style={styles.container}>
      {renderTabSelector()}

      <StaleOfflineBanner isOffline={isOffline} lastSyncedAt={lastUpdated} />

      {loading ? (
        <View style={styles.centerContainer}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.loadingText}>{t('common.loading')}</Text>
        </View>
      ) : error ? (
        <View style={styles.centerContainer}>
          <Text style={styles.errorText}>{error}</Text>
          <TouchableOpacity
            style={styles.retryButton}
            onPress={loadData}
            accessibilityRole="button"
            accessibilityLabel="Coba lagi memuat data akademik"
          >
            <Text style={styles.retryButtonText}>{t('common.retry')}</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <ScrollView style={styles.scrollArea} contentContainerStyle={styles.scrollContent}>
          {activeTab === 'GRADES' && renderGradesContent()}
          {activeTab === 'HOMEWORK' && renderHomeworkContent()}
          {activeTab === 'REPORT_CARDS' && renderReportCardsContent()}
          {activeTab === 'TIMETABLE' && renderTimetableContent()}
        </ScrollView>
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.surface,
  },
  tabBar: {
    flexDirection: 'row',
    backgroundColor: colors.white,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  tabButton: {
    flex: 1,
    minHeight: 44, // PAR-016 touch target >= 44dp
    justifyContent: 'center',
    alignItems: 'center',
    borderBottomWidth: 2,
    borderBottomColor: 'transparent',
    borderRadius: radius.none, // 0px
  },
  tabButtonActive: {
    borderBottomColor: colors.primary,
  },
  tabText: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    fontWeight: typography.fontWeight.medium,
  },
  tabTextActive: {
    color: colors.primary,
    fontWeight: typography.fontWeight.bold,
  },
  scrollArea: {
    flex: 1,
  },
  scrollContent: {
    padding: spacing.base,
    paddingBottom: spacing.xxl,
  },
  centerContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.xl,
  },
  loadingText: {
    marginTop: spacing.md,
    fontSize: typography.fontSize.sm,
    color: colors.muted,
  },
  errorText: {
    fontSize: typography.fontSize.base,
    color: colors.alpa,
    textAlign: 'center',
    marginBottom: spacing.base,
  },
  retryButton: {
    minHeight: 44,
    paddingHorizontal: spacing.xl,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: colors.primary,
    borderRadius: radius.none,
  },
  retryButtonText: {
    color: colors.white,
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
  },
  emptyContainer: {
    paddingVertical: spacing.xxl,
    alignItems: 'center',
    justifyContent: 'center',
  },
  emptyTitle: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
    marginBottom: spacing.xs,
  },
  emptySubtitle: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    textAlign: 'center',
    paddingHorizontal: spacing.lg,
  },
  emptyInnerNote: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
    fontStyle: 'italic',
    padding: spacing.md,
  },
  listContainer: {
    gap: spacing.md,
  },
  card: {
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.none,
    padding: spacing.base,
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: spacing.sm,
  },
  cardHeaderLeft: {
    flex: 1,
    marginRight: spacing.sm,
  },
  subjectName: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  subjectMeta: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
  },
  gradeBadge: {
    alignItems: 'flex-end',
    backgroundColor: colors.primaryLight,
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
    borderRadius: radius.none,
  },
  gradeBadgeScore: {
    fontSize: typography.fontSize.lg,
    fontWeight: typography.fontWeight.bold,
    color: colors.primary,
  },
  gradeBadgeDesc: {
    fontSize: typography.fontSize.xs,
    color: colors.primaryDark,
    fontWeight: typography.fontWeight.medium,
  },
  assessmentList: {
    borderTopWidth: 1,
    borderTopColor: colors.surfaceAlt,
    marginTop: spacing.sm,
    paddingTop: spacing.sm,
    gap: spacing.sm,
  },
  assessmentItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: spacing.xs,
    borderBottomWidth: 1,
    borderBottomColor: colors.surfaceAlt,
  },
  assessmentMain: {
    flex: 1,
    marginRight: spacing.sm,
  },
  assessmentTitleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  assessmentTitle: {
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.semibold,
    color: colors.body,
  },
  typeTag: {
    backgroundColor: colors.surfaceAlt,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: radius.none,
  },
  typeTagText: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    fontWeight: typography.fontWeight.medium,
  },
  assessmentMeta: {
    fontSize: typography.fontSize.xs,
    color: colors.subtle,
    marginTop: 2,
  },
  feedbackText: {
    fontSize: typography.fontSize.xs,
    color: colors.primaryDark,
    fontStyle: 'italic',
    marginTop: 4,
  },
  assessmentScoreBox: {
    alignItems: 'flex-end',
  },
  assessmentScoreText: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  assessmentDescriptorText: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
  },
  statusPill: {
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
    borderRadius: radius.none,
  },
  statusPillText: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
  },
  instructionsText: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
    marginBottom: spacing.sm,
  },
  hwFooter: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: spacing.xs,
  },
  dueText: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
  },
  filesCountText: {
    fontSize: typography.fontSize.xs,
    color: colors.gate,
    fontWeight: typography.fontWeight.medium,
  },
  feedbackBox: {
    backgroundColor: colors.surfaceAlt,
    padding: spacing.sm,
    marginTop: spacing.sm,
    borderRadius: radius.none,
  },
  feedbackLabel: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.body,
  },
  feedbackContent: {
    fontSize: typography.fontSize.xs,
    color: colors.body,
    marginTop: 2,
  },
  arrearsCard: {
    borderLeftWidth: 4,
    borderLeftColor: colors.alpa,
    backgroundColor: '#FFF7F7',
  },
  arrearsHeader: {
    marginBottom: spacing.xs,
  },
  arrearsTitle: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    color: colors.alpa,
  },
  arrearsTerm: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
  },
  arrearsDesc: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
    lineHeight: typography.lineHeight.base,
    marginBottom: spacing.md,
  },
  payButton: {
    minHeight: 44,
    backgroundColor: colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: radius.none,
    paddingHorizontal: spacing.base,
  },
  payButtonText: {
    color: colors.white,
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
  },
  attendanceRow: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    backgroundColor: colors.surfaceAlt,
    paddingVertical: spacing.sm,
    marginVertical: spacing.sm,
    borderRadius: radius.none,
  },
  attendanceItem: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.semibold,
    color: colors.body,
  },
  gradesSnapshotTable: {
    marginTop: spacing.xs,
    marginBottom: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: spacing.xs,
  },
  snapshotRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 4,
  },
  snapshotSubject: {
    flex: 1,
    fontSize: typography.fontSize.xs,
    color: colors.body,
    marginRight: spacing.sm,
  },
  snapshotGrade: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  moreSubjectsText: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    fontStyle: 'italic',
    marginTop: 4,
  },
  downloadButton: {
    minHeight: 44,
    borderWidth: 1,
    borderColor: colors.primary,
    backgroundColor: colors.white,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: radius.none,
    marginTop: spacing.sm,
  },
  downloadButtonText: {
    color: colors.primary,
    fontSize: typography.fontSize.sm,
    fontWeight: typography.fontWeight.bold,
  },
  daySelector: {
    marginBottom: spacing.sm,
  },
  daySelectorContent: {
    gap: spacing.xs,
  },
  dayChip: {
    minHeight: 44,
    paddingHorizontal: spacing.base,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: colors.borderDark,
    borderRadius: radius.none,
    backgroundColor: colors.white,
  },
  dayChipActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  dayChipText: {
    fontSize: typography.fontSize.sm,
    color: colors.body,
    fontWeight: typography.fontWeight.medium,
  },
  dayChipTextActive: {
    color: colors.white,
    fontWeight: typography.fontWeight.bold,
  },
  emptyDayBox: {
    padding: spacing.xl,
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.none,
    alignItems: 'center',
  },
  emptyDayText: {
    fontSize: typography.fontSize.sm,
    color: colors.muted,
  },
  slotCard: {
    flexDirection: 'row',
    backgroundColor: colors.white,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.none,
    padding: spacing.md,
    alignItems: 'center',
  },
  slotTimeBox: {
    width: 90,
    borderRightWidth: 1,
    borderRightColor: colors.border,
    paddingRight: spacing.sm,
    marginRight: spacing.md,
  },
  slotPeriod: {
    fontSize: typography.fontSize.xs,
    fontWeight: typography.fontWeight.bold,
    color: colors.primary,
  },
  slotTime: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
  },
  slotInfoBox: {
    flex: 1,
  },
  slotSubject: {
    fontSize: typography.fontSize.base,
    fontWeight: typography.fontWeight.bold,
    color: colors.heading,
  },
  slotMeta: {
    fontSize: typography.fontSize.xs,
    color: colors.muted,
    marginTop: 2,
  },
  substituteBadge: {
    backgroundColor: colors.substituteLight,
    paddingHorizontal: 6,
    paddingVertical: 2,
    marginTop: 4,
    alignSelf: 'flex-start',
    borderRadius: radius.none,
  },
  substituteBadgeText: {
    fontSize: typography.fontSize.xs,
    color: colors.substitute,
    fontWeight: typography.fontWeight.medium,
  },
});
