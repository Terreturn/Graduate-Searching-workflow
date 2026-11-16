-- Build a new catalog from the graduate program source list. The public query view is source_catalog.
PRAGMA foreign_keys = ON;
BEGIN;

CREATE TABLE sources (
    id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('site', 'post', 'announcement')),
    parent_id TEXT REFERENCES sources(id),
    name TEXT NOT NULL,
    coverage TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE CHECK (url LIKE 'https://%'),
    official_url TEXT CHECK (official_url LIKE 'https://%'),
    notes TEXT NOT NULL,
    application_status TEXT CHECK (application_status IN ('unverified', 'closed', 'open')),
    deadline TEXT,
    CHECK ((kind = 'site') = (parent_id IS NULL)),
    CHECK (kind <> 'site' OR (application_status IS NULL AND deadline IS NULL)),
    CHECK (id <> parent_id)
);
CREATE TABLE source_regions (
    source_id TEXT NOT NULL REFERENCES sources(id),
    region TEXT NOT NULL,
    PRIMARY KEY (source_id, region)
);
CREATE TABLE source_types (
    source_id TEXT NOT NULL REFERENCES sources(id),
    info_type TEXT NOT NULL CHECK (info_type IN
        ('program', 'vacancy', 'funding', 'supervisor_lead', 'application_experience', 'resource_list', 'ranking')),
    PRIMARY KEY (source_id, info_type)
);
CREATE INDEX regions_lookup ON source_regions(region, source_id);
CREATE INDEX types_lookup ON source_types(info_type, source_id);

INSERT INTO sources (id, kind, name, coverage, url, notes) VALUES
('phdportal', 'site', 'PhDportal', '全球，多国项目目录',
 'https://www.phdportal.com/',
 '按学科和目的地寻找博士学位项目；当前申请批次需回到院系核实。'),
('findaphd', 'site', 'FindAPhD', '全球，多国项目与课题',
 'https://www.findaphd.com/phds/',
 '检索具体博士课题和培养项目，可按学科、项目类型和资助情况筛选。'),
('academic-positions', 'site', 'Academic Positions · PhD', '跨国，欧洲岗位较多',
 'https://academicpositions.com/jobs/position/phd',
 '大学与研究机构的博士招聘栏目，可按岗位公告追踪机构、课题和截止日期。'),
('euraxess', 'site', 'EURAXESS Jobs & Opportunities', '欧洲为主，也收录欧洲以外的机会',
 'https://euraxess.ec.europa.eu/jobs/search',
 '检索科研岗位及资助，结合国家、学科、研究人员阶段和职位正文识别博士机会。'),
('jobs-ac-uk', 'site', 'jobs.ac.uk · PhDs', '英国为主，另有海外机会',
 'https://www.jobs.ac.uk/search/phds',
 '跨学科博士招聘、studentship 和奖学金专区。'),
('profellow', 'site', 'ProFellow · Fully Funded Programs and Awards', '跨国，按各项目申请资格核实',
 'https://www.profellow.com/the-directory-of-fully-funded-phd-and-masters-programs-and-full-funding-awards/',
 '收录全额资助的博士、硕士项目和独立奖项；查询时区分学位层级。'),
('ipeds', 'site', 'NCES IPEDS · Completions', '美国',
 'https://nces.ed.gov/ipeds/datacenter/DataFiles.aspx?gotoReportId=7&surveyNumber=3&year=-1',
 '可下载按院校、专业和学位层级划分的历史授予数据；适合建立院校名单。'),
('educanada', 'site', 'EduCanada · Program Search', '加拿大',
 'https://www.educanada.ca/programs-programmes/template-gabarit/programsearch-rechercheprogramme.aspx?lang=eng',
 '按照专业、学位、地区和教学语言查找加拿大院校项目。'),
('chsi', 'site', '中国研究生招生信息网 · 博士专业目录', '中国内地',
 'https://yzst.chsi.com.cn/bsmlcx/cx/dwIndex',
 '招生单位按年度上报博士专业；具体要求以该单位当年的招生简章为依据。'),
('study-in-japan', 'site', 'Study in Japan · Search for Schools', '日本',
 'https://www.studyinjapan.go.jp/en/search-for-schools/school_search.php?lang=en',
 '检索接收国际学生的大学及研究生院，再查对应博士课程和授课语言。'),
('study-australia', 'site', 'Study Australia · Find a Course', '澳大利亚',
 'https://www.studyaustralia.gov.au/en/plan-your-studies/find-a-course',
 '官方课程搜索覆盖 CRICOS 注册教育机构，可进一步查国际学生可申请的研究型博士。'),
('capes-sucupira', 'site', 'CAPES · Plataforma Sucupira', '巴西',
 'https://sucupira-v2.capes.gov.br/programas',
 '经评估与认可的硕博项目目录；本次未能直接读取检索页，可从官方说明页进入。'),
('pan-african-university', 'site', '非洲联盟／Pan African University · Scholarships', '非洲多国，仅泛非大学网络',
 'https://pau-au.africa/admissions/scholarships',
 '按泛非大学招生批次查博士项目和奖学金，每期有独立的申请资格和期限。'),
('muchong', 'site', '小木虫 · 导师招生', '跨国中文社区，中国内地及境外课题组',
 'https://muchong.com/f-430-1',
 '公开招生帖可提供导师、课题、申请材料和团队主页，具体名额需核实。'),
('1point3acres', 'site', '一亩三分地 · 招生广告', '跨国中文社区，侧重海外院系和课题组',
 'https://www.1point3acres.com/bbs/tag-171-1.html',
 '中文招生广告与转帖，适合发现导师、学期及原始申请链接。'),
('reddit-phd', 'site', 'Reddit · r/PhD', '全球，英语社区',
 'https://www.reddit.com/r/PhD/',
 '博士相关公开讨论和资源清单。'),
('reddit-gradadmissions', 'site', 'Reddit · r/gradadmissions', '全球，英语社区',
 'https://www.reddit.com/r/gradadmissions/',
 '研究生申请经验、选校与材料讨论，使用时区分硕士与博士。');

-- Official directories can point to themselves. Commercial directories and forums
-- have discovery URLs; their homepages do not verify individual vacancies.
UPDATE sources SET official_url = url WHERE id IN
    ('euraxess', 'ipeds', 'educanada', 'chsi', 'study-in-japan', 'study-australia', 'pan-african-university');
UPDATE sources SET official_url =
    'https://www.gov.br/capes/pt-br/assuntos/noticias/plataforma-sucupira-conheca-a-ferramenta-que-organiza-e-acompanha-a-pos-graduacao-brasileira'
    WHERE id = 'capes-sucupira';

INSERT INTO sources
    (id, kind, parent_id, name, coverage, url, official_url, notes, application_status, deadline)
VALUES
('muchong-hku-digital-design', 'post', 'muchong', '港大博士招生｜数字化设计与工程管理', '中国香港',
 'https://muchong.com/t-16797364-1', NULL,
 '帖子列出研究主题、申请材料和课题组联系方式。具体名额与资助尚待发布单位确认。', 'unverified', NULL),
('reddit-phd-scholarship-sites', 'post', 'reddit-phd', 'Best sites/databases to find PhD scholarships', '跨国资源讨论',
 'https://www.reddit.com/r/PhD/comments/16fpa6m/best_sitesdatabases_to_find_phd_scholarships/', NULL,
 '社区整理寻找博士奖学金的网站，适合发现新来源。', NULL, NULL),
('pau-scholarships-2026-27', 'announcement', 'pan-african-university',
 'Pan African University · 2026–2027 学年奖学金招生公告', '非洲多国，本期泛非大学成员研究所',
 'https://www.au.int/en/announcements/20251115/pan-african-university-call-scholarships-applications-academic-year-2026-2027',
 'https://www.au.int/en/announcements/20251115/pan-african-university-call-scholarships-applications-academic-year-2026-2027',
 '非盟公布的硕士及博士招生批次；公告列明博士申请条件、奖学金和截止日。', 'closed', '2025-12-15');

-- A global tag denotes international discovery; it does not imply exhaustive country coverage.
INSERT INTO source_regions VALUES
('phdportal', 'global'), ('findaphd', 'global'),
('academic-positions', 'global'), ('academic-positions', 'region:Europe'),
('euraxess', 'global'), ('euraxess', 'region:Europe'),
('jobs-ac-uk', 'global'), ('jobs-ac-uk', 'region:Europe'), ('jobs-ac-uk', 'country:GB'),
('profellow', 'global'),
('ipeds', 'region:North America'), ('ipeds', 'country:US'),
('educanada', 'region:North America'), ('educanada', 'country:CA'),
('chsi', 'region:Asia'), ('chsi', 'country:CN'),
('study-in-japan', 'region:Asia'), ('study-in-japan', 'country:JP'),
('study-australia', 'region:Oceania'), ('study-australia', 'country:AU'),
('capes-sucupira', 'region:South America'), ('capes-sucupira', 'country:BR'),
('pan-african-university', 'region:Africa'),
('muchong', 'global'), ('muchong', 'country:CN'), ('muchong', 'region:Asia'),
('1point3acres', 'global'), ('reddit-phd', 'global'), ('reddit-gradadmissions', 'global'),
('muchong-hku-digital-design', 'region:Asia'), ('muchong-hku-digital-design', 'country:HK'),
('reddit-phd-scholarship-sites', 'global'), ('pau-scholarships-2026-27', 'region:Africa');

INSERT INTO source_types VALUES
('phdportal', 'program'),
('findaphd', 'program'), ('findaphd', 'vacancy'), ('findaphd', 'funding'),
('academic-positions', 'vacancy'), ('euraxess', 'vacancy'), ('euraxess', 'funding'),
('jobs-ac-uk', 'vacancy'), ('jobs-ac-uk', 'funding'),
('profellow', 'program'), ('profellow', 'funding'),
('ipeds', 'program'), ('educanada', 'program'), ('chsi', 'program'),
('study-in-japan', 'program'), ('study-australia', 'program'), ('capes-sucupira', 'program'),
('pan-african-university', 'program'), ('pan-african-university', 'funding'),
('muchong', 'supervisor_lead'), ('muchong', 'vacancy'),
('1point3acres', 'supervisor_lead'), ('1point3acres', 'vacancy'),
('reddit-phd', 'application_experience'), ('reddit-phd', 'resource_list'),
('reddit-gradadmissions', 'application_experience'), ('reddit-gradadmissions', 'resource_list'),
('muchong-hku-digital-design', 'supervisor_lead'), ('muchong-hku-digital-design', 'funding'),
('reddit-phd-scholarship-sites', 'resource_list'),
('pau-scholarships-2026-27', 'program'), ('pau-scholarships-2026-27', 'funding');

CREATE TABLE ranking_editions (
    source_id TEXT PRIMARY KEY NOT NULL REFERENCES sources(id),
    publisher TEXT NOT NULL,
    ranking_scope TEXT NOT NULL CHECK (ranking_scope IN ('overall', 'subject', 'graduate_school', 'phd_program')),
    ranking_object TEXT NOT NULL,
    ranking_year INTEGER NOT NULL CHECK (typeof(ranking_year) = 'integer' AND ranking_year BETWEEN 1900 AND 9999),
    disciplines TEXT NOT NULL CHECK (json_valid(disciplines) AND json_type(disciplines) = 'array'
                                     AND json_array_length(disciplines) > 0)
);

-- Each row identifies one named ranking edition. For rolling portals the discovery
-- URL is a dated publisher release and official_url is the ranking itself.
INSERT INTO sources (id, kind, name, coverage, url, official_url, notes) VALUES
('qs-world-2027', 'site', 'QS World University Rankings 2027', '全球',
 'https://www.topuniversities.com/rankings-release-summaries/world-university-rankings-2027-release-summary',
 'https://www.topuniversities.com/world-university-rankings',
 '整所大学的综合表现。榜单入口随年度更新，发布记录固定到 2027 版。'),
('qs-subject-2026', 'site', 'QS World University Rankings by Subject 2026', '全球',
 'https://www.topuniversities.com/article/tags/rankings-release-summaries/qs-world-university-rankings-subject-2026-release-summary',
 'https://www.topuniversities.com/subject-rankings',
 '按五大学科领域及其下的 55 个细分学科比较大学。'),
('the-world-2027', 'site', 'THE World University Rankings 2027', '全球',
 'https://www.timeshighereducation.com/world-university-rankings/2027/world-ranking',
 'https://www.timeshighereducation.com/world-university-rankings/2027/world-ranking',
 '研究型大学综合表现。'),
('the-subject-2026', 'site', 'THE World University Rankings by Subject 2026', '全球',
 'https://www.timeshighereducation.com/press-releases/world-university-rankings-subject-2026-out-now',
 'https://www.timeshighereducation.com/world-university-rankings/by-subject',
 '按 11 个学科大类评估大学表现，指标权重按学科调整。'),
('shanghai-arwu-2026', 'site', '软科世界大学学术排名（ARWU）2026', '全球',
 'https://www.shanghairanking.com/rankings/arwu/2026',
 'https://www.shanghairanking.com/rankings/arwu/2026',
 '按科研成果、学术奖项等指标评价整所大学的学术表现。'),
('shanghai-gras-2025', 'site', '软科世界一流学科排名（GRAS）2025', '全球',
 'https://www.shanghairanking.com/rankings/gras/2025',
 'https://www.shanghairanking.com/rankings/gras/2025',
 '按自然科学、工程、生命科学、医学和社会科学五个领域下的 57 个学科评估大学。'),
('usnews-grad-2026', 'site', 'U.S. News Best Graduate Schools 2026', '美国',
 'https://www.prnewswire.com/news-releases/us-news-unveils-2026-best-graduate-schools-302735056.html',
 'https://www.usnews.com/best-graduate-schools',
 '年份依据 U.S. News 发布的 2026 版公告。学科字段列举所涉及的领域；使用具体分榜时还需核对其更新年份及学位层级。'),
('trip-ir-phd-2024', 'site', 'TRIP · Best International Relations Schools for PhD Programs for Academics 2024', '跨国，包括美国、英国等地的项目',
 'https://foreignpolicy.com/2024/07/30/international-relations-school-rankings-university-undergraduate-masters-phd-programs/',
 'https://foreignpolicy.com/2024/07/30/international-relations-school-rankings-university-undergraduate-masters-phd-programs/',
 '同页含不同学位和调查群体的表格。此条对应国际关系教师评选的面向学术职业的博士项目表。'),
('pgr-philosophy-2024', 'site', 'Philosophical Gourmet Report · Overall Rankings 2024', '英语世界，涵盖美国、英国、加拿大、澳大利亚、新西兰及新加坡等地的受评项目',
 'https://philosophicalgourmet.com/overall-rankings-2024/',
 'https://philosophicalgourmet.com/overall-rankings-2024/',
 '按同行对哲学项目师资的声誉评分排名。页面含英语世界 Top 50 及分地区结果。');

INSERT INTO ranking_editions (source_id, publisher, ranking_scope, ranking_object, ranking_year, disciplines) VALUES
('qs-world-2027', 'QS', 'overall', '整所大学', 2027, '["Multidisciplinary"]'),
('qs-subject-2026', 'QS', 'subject', '大学在指定学科的表现', 2026,
 '["Arts & Humanities","Engineering & Technology","Life Sciences & Medicine","Natural Sciences","Social Sciences & Management"]'),
('the-world-2027', 'Times Higher Education', 'overall', '研究型大学整体', 2027, '["Multidisciplinary"]'),
('the-subject-2026', 'Times Higher Education', 'subject', '大学在指定学科大类的表现', 2026,
 '["Arts and Humanities","Business and Economics","Computer Science","Education Studies","Engineering","Law","Life Sciences","Medical and Health","Physical Sciences","Psychology","Social Sciences"]'),
('shanghai-arwu-2026', 'ShanghaiRanking', 'overall', '整所大学的学术表现', 2026, '["Multidisciplinary"]'),
('shanghai-gras-2025', 'ShanghaiRanking', 'subject', '大学在指定学科的科研表现', 2025,
 '["Natural Sciences","Engineering","Life Sciences","Medical Sciences","Social Sciences"]'),
('usnews-grad-2026', 'U.S. News & World Report', 'graduate_school', '美国研究生院及研究型、专业型研究生项目（多学位层级）', 2026,
 '["Business","Education","Engineering","Law","Medicine","Nursing","Health","Sciences","Computer Science","Fine Arts"]'),
('trip-ir-phd-2024', 'Foreign Policy × William & Mary TRIP', 'phd_program', '面向学术职业的国际关系博士项目', 2024,
 '["International Relations"]'),
('pgr-philosophy-2024', 'Philosophical Gourmet Report', 'phd_program', '哲学博士项目的师资声誉', 2024, '["Philosophy"]');

INSERT INTO source_types SELECT source_id, 'ranking' FROM ranking_editions;
INSERT INTO source_regions VALUES
('qs-world-2027', 'global'), ('qs-subject-2026', 'global'),
('the-world-2027', 'global'), ('the-subject-2026', 'global'),
('shanghai-arwu-2026', 'global'), ('shanghai-gras-2025', 'global'),
('usnews-grad-2026', 'country:US'), ('usnews-grad-2026', 'region:North America'),
('trip-ir-phd-2024', 'global'), ('trip-ir-phd-2024', 'country:US'), ('trip-ir-phd-2024', 'country:GB'),
('trip-ir-phd-2024', 'region:North America'), ('trip-ir-phd-2024', 'region:Europe'),
('pgr-philosophy-2024', 'region:North America'), ('pgr-philosophy-2024', 'region:Europe'),
('pgr-philosophy-2024', 'region:Oceania'), ('pgr-philosophy-2024', 'region:Asia'),
('pgr-philosophy-2024', 'country:US'), ('pgr-philosophy-2024', 'country:GB'),
('pgr-philosophy-2024', 'country:CA'), ('pgr-philosophy-2024', 'country:AU'),
('pgr-philosophy-2024', 'country:NZ'), ('pgr-philosophy-2024', 'country:SG');

-- Applicability of this exact directory, post or ranking edition for a degree search.
CREATE TABLE source_degrees (
    source_id TEXT NOT NULL REFERENCES sources(id),
    degree TEXT NOT NULL CHECK (degree IN ('master','PhD','professional_doctorate')),
    PRIMARY KEY (source_id, degree)
);
CREATE INDEX degrees_lookup ON source_degrees(degree, source_id);

INSERT INTO sources (id, kind, name, coverage, url, official_url, notes) VALUES
('erasmus-mundus-masters', 'site', 'Erasmus Mundus Joint Masters', '欧洲为主的跨国联合硕士',
 'https://erasmus-plus.ec.europa.eu/opportunities/individuals/students/erasmus-mundus-joint-masters',
 'https://erasmus-plus.ec.europa.eu/opportunities/individuals/students/erasmus-mundus-joint-masters',
 '联合硕士目录和奖学金说明。择优奖学金的金额及资格按具体项目和批次核实。');
INSERT INTO source_regions VALUES ('erasmus-mundus-masters', 'region:Europe');
INSERT INTO source_types VALUES ('erasmus-mundus-masters', 'program'), ('erasmus-mundus-masters', 'funding');

-- The original entries were curated for PhD searches. Custom entries remain unclassified.
INSERT INTO source_degrees SELECT id, 'PhD' FROM sources WHERE id IN
    ('phdportal','findaphd','academic-positions','euraxess','jobs-ac-uk','profellow','ipeds','educanada',
     'chsi','study-in-japan','study-australia','capes-sucupira','pan-african-university','muchong',
     '1point3acres','reddit-phd','reddit-gradadmissions','muchong-hku-digital-design',
     'reddit-phd-scholarship-sites','pau-scholarships-2026-27','qs-world-2027','qs-subject-2026',
     'the-world-2027','the-subject-2026','shanghai-arwu-2026','shanghai-gras-2025',
     'usnews-grad-2026','trip-ir-phd-2024','pgr-philosophy-2024');
INSERT INTO source_degrees SELECT id, 'master' FROM sources WHERE id IN
    ('erasmus-mundus-masters','profellow','ipeds','educanada','study-in-japan','study-australia',
     'capes-sucupira','pan-african-university','pau-scholarships-2026-27','reddit-gradadmissions',
     'qs-world-2027','qs-subject-2026','the-world-2027','the-subject-2026','shanghai-arwu-2026',
     'shanghai-gras-2025','usnews-grad-2026');
-- University and subject rankings provide institutional context, not program-level ranks.
INSERT INTO source_degrees SELECT id, 'professional_doctorate' FROM sources WHERE id IN
    ('ipeds','qs-world-2027','qs-subject-2026','the-world-2027','the-subject-2026',
     'shanghai-arwu-2026','shanghai-gras-2025','usnews-grad-2026');

CREATE VIEW source_catalog AS
SELECT s.*,
       (SELECT json_group_array(region) FROM
           (SELECT region FROM source_regions WHERE source_id = s.id ORDER BY region)) AS regions,
       (SELECT json_group_array(info_type) FROM
           (SELECT info_type FROM source_types WHERE source_id = s.id ORDER BY info_type)) AS info_types,
       r.publisher, r.ranking_scope, r.ranking_object, r.ranking_year, r.disciplines,
       (SELECT json_group_array(degree) FROM
           (SELECT degree FROM source_degrees WHERE source_id = s.id ORDER BY degree)) AS degrees
FROM sources AS s LEFT JOIN ranking_editions AS r ON r.source_id = s.id;
COMMIT;
