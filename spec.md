# Obce v kostce
This is a spec for a data aggregation tool Obce v kostce (proposal [here]("./Specifikace Obce v kostce.pdf") ). It takes data on municipalities and puts it in one place for interested mayors, council workers and members and researchers to use. Our goal is to provide municipalities a simple way to view data about themselves in context. Our data principles are: 
- Data is always traceable back to its source 
- Data is being updated, so that the tool isn't stuck with just 5 year old data 

The tool consists of the following layers:

1. Data layer -- core is a PostgreSQL database that stores data and metadata.
2. Dashboards -- for a given obce, populates thematic dashboards that dispaly data and puts them in context of municipalities in the vicinity and CZ averages
3. Data explorer -- easily find a number/time series (eg. "How many property crimes have we had in the past few years?"), create and download a chart of table from that 
4. Data exports -- based on mucicipality and theme (see demo "obcevkostce.ceskepriority.cz")
5. API -- data exports from the data layer (see demo "obcevkostce.ceskepriority.cz/api")


## Sources 
ČSÚ / CZSO: Czech statistical office, a cornerstone source. Mainly demographics. Accesible through the [DataStat API](https://csu.gov.cz/zakladni-informace-pro-pouziti-api-datastatu)
Monitor státní pokladny: Data on all budgetary matters 
Policie ČR (Mapa kriminality)
Agentura pro sociální začleňování: Social exclusion index and its components (https://mmr.gov.cz/cs/microsites/socialni-zaclenovani/index-socialniho-vylouceni)
EKČR open data (number of debtors per obce) 


## Database spec 
NN = not null

**Source**
Source ID PK 
Source name TXT NN 
Homepage URL 

**Dataset**
Dataset ID TXT (PK) -- mirrors source conventions
Source ID (FK Source)
Dataset name TXT NN

**Release**
Release ID (PK, Serial)
Dataset ID (FK dataset) 
Label TXT NN 
Published at Date NN 
Retrieved at Date NN 
Source url TXT 
raw_uri URL NN -- link to raw dataset file 
loading_code URL NN -- link to code that loaded the relase 
code version GIT SHA NN 
supersedes (FK relase id) -- for revisions etc 
notes TXT


**Aggregation method**
Enum: SUM, WEIGHTED MEAN, MEAN, NONE

**Indicator**
Indicator ID TXT (PK)
Indicator name TXT NN 
Unit of measure TXT
Definition TXT
Normalisable BOOL -- can it be computed eg. per capita or per debtor?
Normalisation indicaotr (FK Indicator) -- eg. population size
Aggregation method Aggregation method NN
Weight indicator (FK Indicator) -- for weighted mean


**Series**
Series ID TXT (PK)
Indicator ID (FK indicator) 
Breakdown JSONB NN default {} (eg. "{"sex":"F","age":"65+"}")
unique (Indicator ID, Breakdown)

**Observation**
Series ID (FK series)
Municipality ID (FK municipality)
Period DATERANGE NN
Value NUMERIC NN
Release ID (FK release) NN
PK Series ID + Muncipality ID + Period + Release ID

All except admin forbidden from updating, deleting

**Current observations** (materialised view)
View from observations, just the most recent relase for each observation

**Municipality**
Municipality ID (PK) -- copies ČSÚ id obce
Municipality name TXT NN
Municipality type TXT -- Obce < Městys < Město < Statutární město < Krajské město < Hlavní město

**Group of municipalities**
Group ID (PK) -- mirrors ČSÚ if available (Kraje, obce)
Group Name TXT NN
Group type TXT -- eg. ORP, Kraj, Společenství obcí
Parent Group (FK Group) -- for eg. ORP <- Okres <- Kraj 

**Municipality<->Group**
Group ID (FK Group) 
Muncipality ID (FK Municipality)


